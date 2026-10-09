#!/usr/bin/env python3
"""Mac-side client for the server's admin API (docs/DEPLOYMENT_PLAN.md, P6.3).

Standard library only, so it runs with plain `python3`. Reads ~/.couchcinema/env
(KEY=value lines): CC_SERVER (e.g. http://192.168.1.183:8000) and ADMIN_TOKEN.

    cc.py export <out.db>                         download a consistent copy of the server's database
    cc.py diff-catalogue <before.db> <after.db> <out.jsonl>
                                                  what changed between two copies, keyed by (media_type, tmdb_id)
    cc.py push-patch <file.jsonl>                 apply a patch on the server
    cc.py build-bundle <out.tar.gz> --metrics <metrics.json> [--tiger DIR] [--vibe DIR]
                       [--db-before X.db --db-after Y.db] [--gate passed|failed] [--source-db X.db]
    cc.py push-bundle <file.tar.gz>               install a bundle on the server
    cc.py model-status                            print which model the server runs, with its metrics
    cc.py rollback-model                          go back to the previous model on the server
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tarfile
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Optional, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
from services.catalogue_patch import ALLOWED_COLUMNS, REPLACE_TABLES  # noqa: E402

ENV_FILE = os.path.expanduser(os.environ.get("CC_ENV_FILE", "~/.couchcinema/env"))


class CCError(RuntimeError):
    pass


def load_env(path: str = ENV_FILE) -> Dict[str, str]:
    env: Dict[str, str] = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return {**env, **{k: v for k, v in os.environ.items() if k in ("CC_SERVER", "ADMIN_TOKEN")}}


def _request(method: str, path: str, *, data: Any = None, length: Optional[int] = None,
             timeout: int = 600) -> Any:
    env = load_env()
    server, token = env.get("CC_SERVER", "").rstrip("/"), env.get("ADMIN_TOKEN", "")
    if not server or not token:
        raise CCError(f"CC_SERVER and ADMIN_TOKEN must be set in {ENV_FILE}")
    headers = {"Authorization": f"Bearer {token}"}
    if length is not None:
        headers["Content-Length"] = str(length)
        headers["Content-Type"] = "application/octet-stream"
    req = urllib.request.Request(server + path, data=data, method=method, headers=headers)
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:500]
        raise CCError(f"{method} {path} -> HTTP {e.code}: {detail}") from e
    except urllib.error.URLError as e:
        raise CCError(f"cannot reach {server} ({e.reason}). Is Tailscale up?") from e


def _json(resp: Any) -> Dict[str, Any]:
    with resp:
        return json.loads(resp.read().decode("utf-8"))


# -- export ----------------------------------------------------------------
def export_db(out: str) -> int:
    tmp = out + ".part"
    with _request("GET", "/admin/export") as resp, open(tmp, "wb") as f:
        max_id = int(resp.headers.get("X-Media-Items-Max-Id", "0"))
        while chunk := resp.read(1 << 20):
            f.write(chunk)
    conn = sqlite3.connect(tmp)
    try:
        verdict = conn.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        conn.close()
    if verdict != "ok":
        os.unlink(tmp)
        raise CCError(f"the downloaded database failed its integrity check: {verdict}")
    os.replace(tmp, out)
    print(f"exported {out} ({os.path.getsize(out) / 1e6:.1f} MB, highest item id {max_id})")
    return max_id


# -- diff ------------------------------------------------------------------
def _items(path: str) -> Dict[Tuple[str, int], Dict[str, Any]]:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        have = {r[1] for r in conn.execute("PRAGMA table_info(media_items)")}
        cols = sorted(ALLOWED_COLUMNS & have)
        sql = f"SELECT media_type, tmdb_id, {', '.join(cols)} FROM media_items WHERE tmdb_id IS NOT NULL"
        return {(r["media_type"], r["tmdb_id"]): {c: r[c] for c in cols} for r in conn.execute(sql)}
    finally:
        conn.close()


def _table(path: str, table: str) -> List[Dict[str, Any]]:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        cols = sorted(REPLACE_TABLES[table])
        return [{c: r[c] for c in cols} for r in conn.execute(f"SELECT {', '.join(cols)} FROM {table} ORDER BY id")]
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


def diff_lines(before: str, after: str) -> Iterator[str]:
    """Patch lines turning `before` into `after`: new items in full, changed items only their changed columns."""
    old, new = _items(before), _items(after)
    for key in sorted(new):
        fields = new[key]
        if key in old:
            fields = {c: v for c, v in fields.items() if old[key].get(c) != v}
            if not fields:
                continue
        else:
            fields = {c: v for c, v in fields.items() if v is not None}
        yield json.dumps({"media_type": key[0], "tmdb_id": key[1], "fields": fields}, separators=(",", ":"))
    for table in REPLACE_TABLES:
        rows = _table(after, table)
        if rows != _table(before, table):
            yield json.dumps({"table": table, "rows": rows}, separators=(",", ":"))


def diff_catalogue(before: str, after: str, out: str) -> int:
    n = 0
    with open(out, "w", encoding="utf-8") as f:
        for line in diff_lines(before, after):
            f.write(line + "\n")
            n += 1
    print(f"{n} change(s) written to {out}")
    return n


# -- push ------------------------------------------------------------------
def push_patch(path: str) -> Dict[str, Any]:
    with open(path, "rb") as f:
        result = _json(_request("POST", "/admin/catalogue-patch", data=f, length=os.path.getsize(path)))
    print(f"patch applied: {result}")
    return result


def push_bundle(path: str) -> Dict[str, Any]:
    with open(path, "rb") as f:
        result = _json(_request("POST", "/admin/model-bundle", data=f, length=os.path.getsize(path)))
    print(f"bundle installed: {result}")
    return result


def model_status() -> Dict[str, Any]:
    return _json(_request("GET", "/admin/model-status"))


# -- bundle ----------------------------------------------------------------
def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# What the server's app actually reads from each folder (training leaves other files around).
TIGER_FILES = ("predictor_int8.onnx", "semantic_ids.json", "prefix_trie.json", "item_map.json", "config.json")
VIBE_FILES = ("item_vectors.npy", "item_ids.json", "config.json", "query_encoder.onnx", "anchors.npy", "centroids.npy")


def build_bundle(out: str, *, metrics: Dict[str, Any], tiger: Optional[str], vibe: Optional[str],
                 db_before: Optional[str], db_after: Optional[str], gate: str,
                 source_db: Optional[str]) -> Dict[str, Any]:
    files: Dict[str, str] = {}   # path inside the bundle -> source on disk
    if tiger:
        for n in TIGER_FILES:
            if os.path.exists(os.path.join(tiger, n)):
                files[f"tiger/{n}"] = os.path.join(tiger, n)
    if vibe:
        for n in VIBE_FILES:
            if os.path.exists(os.path.join(vibe, n)):
                files[f"vibe/{n}"] = os.path.join(vibe, n)
        tok = os.path.join(vibe, "tokenizer")
        for dp, _, fns in os.walk(tok):
            for fn in fns:
                files["vibe/" + os.path.relpath(os.path.join(dp, fn), vibe)] = os.path.join(dp, fn)
    if not files:
        raise CCError("nothing to bundle: give --tiger and/or --vibe folders that hold model files")
    patch_path = None
    if db_before and db_after:
        patch_path = out + ".db_patch.jsonl"
        diff_catalogue(db_before, db_after, patch_path)
        files["db_patch.jsonl"] = patch_path

    max_id = 0
    if source_db:
        conn = sqlite3.connect(source_db)
        max_id = conn.execute("SELECT COALESCE(MAX(id), 0) FROM media_items").fetchone()[0]
        conn.close()
    try:
        sha = subprocess.run(["git", "-C", ROOT, "rev-parse", "--short", "HEAD"], capture_output=True,
                             text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        sha = "unknown"
    now = datetime.now(timezone.utc)
    manifest = {
        "bundle_id": "bundle-" + now.strftime("%Y%m%dT%H%M%SZ"),
        "created_at": now.isoformat(timespec="seconds"),
        "git_sha": sha,
        "source_db_max_id": max_id,
        "files": {rel: _sha256(src) for rel, src in sorted(files.items())},
        "metrics": metrics,
        "gate": gate,
    }
    manifest_path = out + ".manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    with tarfile.open(out, "w:gz") as tar:
        tar.add(manifest_path, arcname="manifest.json")
        for rel, src in sorted(files.items()):
            tar.add(src, arcname=rel)
    os.unlink(manifest_path)
    if patch_path:
        os.unlink(patch_path)
    print(f"built {out} ({os.path.getsize(out) / 1e6:.1f} MB) as {manifest['bundle_id']}, gate {gate}")
    return manifest


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("export").add_argument("out")
    d = sub.add_parser("diff-catalogue")
    d.add_argument("before"); d.add_argument("after"); d.add_argument("out")  # noqa: E702
    sub.add_parser("push-patch").add_argument("file")
    b = sub.add_parser("build-bundle")
    b.add_argument("out")
    b.add_argument("--metrics", required=True, help="JSON file of numbers, e.g. {\"hit_rate\": 0.31}")
    b.add_argument("--tiger"); b.add_argument("--vibe")  # noqa: E702
    b.add_argument("--db-before"); b.add_argument("--db-after"); b.add_argument("--source-db")  # noqa: E702
    b.add_argument("--gate", choices=["passed", "failed"], default="passed")
    sub.add_parser("push-bundle").add_argument("file")
    sub.add_parser("model-status")
    sub.add_parser("rollback-model")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "export":
            export_db(a.out)
        elif a.cmd == "diff-catalogue":
            diff_catalogue(a.before, a.after, a.out)
        elif a.cmd == "push-patch":
            push_patch(a.file)
        elif a.cmd == "build-bundle":
            with open(a.metrics, "r", encoding="utf-8") as f:
                metrics = json.load(f)
            build_bundle(a.out, metrics=metrics, tiger=a.tiger, vibe=a.vibe, db_before=a.db_before,
                         db_after=a.db_after, gate=a.gate, source_db=a.source_db)
        elif a.cmd == "push-bundle":
            push_bundle(a.file)
        elif a.cmd == "model-status":
            print(json.dumps(model_status(), indent=2))
        elif a.cmd == "rollback-model":
            print(_json(_request("POST", "/admin/model-rollback")))
    except CCError as e:
        print(f"cc.py: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
