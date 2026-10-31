"""
Offline tests for the admin API and model bundles (services/admin_api.py, services/model_bundle.py)

Throwaway database and models folder in a tempdir; the model smoke test is stubbed except in
the case that checks a bundle holding unusable model files is turned away.

    uv run python tests/test_admin_api.py
"""

import hashlib
import io
import json
import os
import sqlite3
import sys
import tarfile
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

failures = []


def check(label, condition, detail=""):
    if condition:
        print(f"  ✅ {label}")
    else:
        print(f"  ❌ {label} — got: {detail}")
        failures.append(label)


def make_bundle(bundle_id, files, gate="passed", lie_about=None, extra=None):
    """A .tar.gz as bytes. files: {path: bytes}. lie_about: a path whose checksum is wrong."""
    manifest = {
        "bundle_id": bundle_id, "created_at": "2026-10-10T03:30:00Z", "git_sha": "abc", "source_db_max_id": 3,
        "files": {p: hashlib.sha256(b"x" if p == lie_about else d).hexdigest() for p, d in files.items()},
        "metrics": {"hit_rate": 0.5}, "gate": gate,
    }
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in {**files, "manifest.json": json.dumps(manifest).encode(), **(extra or {})}.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def sem(*ids):
    return json.dumps({"items": {str(i): [0, 0, 0] for i in ids}}).encode()


with tempfile.TemporaryDirectory() as tmp:
    db_file = os.path.join(tmp, "c.db")
    models = os.path.join(tmp, "models")
    os.makedirs(os.path.join(models, "releases"))
    cfg_file = os.path.join(tmp, "config.yaml")
    with open(cfg_file, "w", encoding="utf-8") as f:
        json.dump({
            "media_folders": [os.path.join(tmp, "Movies"), os.path.join(tmp, "Shows")],
            "jellyfin_library": {"movie_dir": os.path.join(tmp, "Movies"), "tv_dir": os.path.join(tmp, "Shows")},
            "torrent": {"download_dir": os.path.join(tmp, "downloads")},
        }, f)
    os.environ["CONFIG_PATH"] = cfg_file
    os.environ["DATABASE_PATH"] = db_file
    os.environ["MODELS_DIR"] = os.path.join(models, "current")
    os.environ.pop("ADMIN_TOKEN", None)
    import server  # noqa: E402
    from web import state  # noqa: E402
    from fastapi.testclient import TestClient
    from services import model_bundle

    conn = sqlite3.connect(db_file)
    for i in (1, 2, 3):
        conn.execute("INSERT INTO media_items (media_type, tmdb_id, title) VALUES ('movie', ?, ?)", (i, f"Film {i}"))
    conn.commit()
    conn.close()

    # Real models are not built in CI: pretend loading them works.
    state.generative_retriever.reload = lambda: True
    state.vibe_index.reload = lambda: True
    state.vibe_index.is_ready = lambda: True
    real_smoke = model_bundle.smoke_test
    model_bundle.smoke_test = lambda folder, db: None
    client = TestClient(server.app)
    H = {"Authorization": "Bearer s3cret"}

    print("auth")
    check("404 when ADMIN_TOKEN is unset", client.get("/admin/export", headers=H).status_code == 404)
    os.environ["ADMIN_TOKEN"] = "s3cret"
    check("401 without a token", client.get("/admin/export").status_code == 401)
    check("401 with a wrong token", client.get("/admin/export", headers={"Authorization": "Bearer nope"}).status_code == 401)

    print("export")
    r = client.get("/admin/export", headers=H)
    check("200 and the max id header", r.status_code == 200 and r.headers.get("x-media-items-max-id") == "3",
          (r.status_code, r.headers))
    out = os.path.join(tmp, "got.db")
    with open(out, "wb") as f:
        f.write(r.content)
    got = sqlite3.connect(out)
    check("a usable copy of the database", got.execute("SELECT COUNT(*) FROM media_items").fetchone()[0] == 3
          and got.execute("PRAGMA integrity_check").fetchone()[0] == "ok")
    got.close()

    print("catalogue patch")
    body = "\n".join(json.dumps(x) for x in [
        {"media_type": "movie", "tmdb_id": 77, "fields": {"title": "Patched In"}},
        {"media_type": "movie", "tmdb_id": 1, "fields": {"facets": "{}"}}])
    r = client.post("/admin/catalogue-patch", headers=H, content=body)
    check("patch counts", r.status_code == 200 and r.json()["inserted"] == 1 and r.json()["updated"] == 1, r.text)
    r = client.post("/admin/catalogue-patch", headers=H, content=json.dumps(
        {"media_type": "movie", "tmdb_id": 1, "fields": {"user_rating": 3}}))
    check("a user column is a 400", r.status_code == 400, r.text)

    print("model bundles")
    good = make_bundle("b1", {"tiger/semantic_ids.json": sem(1, 2, 3), "vibe/item_ids.json": b"[1, 2]",
                              "db_patch.jsonl": json.dumps(
                                  {"media_type": "movie", "tmdb_id": 2, "fields": {"vibe_cluster_id": 5}}).encode()})
    r = client.post("/admin/model-bundle", headers=H, content=good)
    check("good bundle installs", r.status_code == 200 and r.json()["installed"] == "b1", r.text)
    link = os.path.join(models, "current")
    check("current points at it (relative link)", os.readlink(link) == os.path.join("releases", "b1"), os.readlink(link))
    check("its db patch was applied", sqlite3.connect(db_file).execute(
        "SELECT vibe_cluster_id FROM media_items WHERE tmdb_id=2").fetchone() == (5,))
    check("healthz shows the bundle", client.get("/healthz").json()["model_bundle"] == "b1")
    check("model-status agrees", client.get("/admin/model-status", headers=H).json()["current"] == "b1")

    r = client.post("/admin/model-bundle", headers=H, content=good)
    check("installing the same id twice is a 409", r.status_code == 409, r.text)

    bad_sum = make_bundle("b2", {"tiger/semantic_ids.json": sem(1)}, lie_about="tiger/semantic_ids.json")
    r = client.post("/admin/model-bundle", headers=H, content=bad_sum)
    check("bad checksum refused", r.status_code == 422 and "checksum" in r.text, r.text)

    r = client.post("/admin/model-bundle", headers=H, content=make_bundle("b3", {"tiger/semantic_ids.json": sem(1, 999)}))
    check("a model naming a missing item id is refused", r.status_code == 422 and "not in the database" in r.text, r.text)

    r = client.post("/admin/model-bundle", headers=H, content=make_bundle(
        "b4", {"tiger/semantic_ids.json": sem(1)}, gate="failed"))
    check("a failed gate is refused", r.status_code == 422 and "gate" in r.text, r.text)

    r = client.post("/admin/model-bundle", headers=H, content=make_bundle(
        "b5", {"tiger/semantic_ids.json": sem(1)}, extra={"sneaky.txt": b"hi"}))
    check("a file the manifest does not list is refused", r.status_code == 422, r.text)

    r = client.post("/admin/model-bundle", headers=H, content=b"not a tarball")
    check("garbage is refused", r.status_code == 422, r.text)
    check("none of the refused ones left anything behind",
          sorted(os.listdir(os.path.join(models, "releases"))) == ["b1"], os.listdir(os.path.join(models, "releases")))
    check("current still b1", os.readlink(link).endswith("b1"))

    print("real smoke test turns away unusable model files")
    model_bundle.smoke_test = real_smoke
    r = client.post("/admin/model-bundle", headers=H, content=make_bundle(
        "b6", {"tiger/semantic_ids.json": sem(1), "tiger/prefix_trie.json": b"{}", "tiger/predictor_int8.onnx": b"junk"}))
    check("422 smoke test failure", r.status_code == 422 and "smoke test" in r.text, r.text)
    check("and current is unchanged", os.readlink(link).endswith("b1"))

    print("reload failure restores the old model")
    model_bundle.smoke_test = lambda folder, db: None
    flags = {"fail_next": False}
    real = state.reload_models

    def flaky():
        if flags["fail_next"]:
            flags["fail_next"] = False
            raise RuntimeError("boom")
        real()

    model_bundle2 = make_bundle("b7", {"tiger/semantic_ids.json": sem(2)})
    # Install through the module with the flaky reloader (the app's router holds the original).
    with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as f:
        f.write(model_bundle2)
    flags["fail_next"] = True
    try:
        model_bundle.install_bundle(f.name, db_path=db_file, current_link=link, reload_models=flaky)
        check("failed reload raises", False)
    except model_bundle.BundleError as e:
        check("failed reload raises and says it restored", e.status == 500 and "restored" in str(e), str(e))
    os.unlink(f.name)
    check("current went back to b1", os.readlink(link).endswith("b1"))
    check("the broken release was removed", not os.path.exists(os.path.join(models, "releases", "b7")))

    print("rollback and pruning")
    for n in ("b8", "b9", "b10"):
        r = client.post("/admin/model-bundle", headers=H, content=make_bundle(n, {"tiger/semantic_ids.json": sem(1)}))
        assert r.status_code == 200, r.text
    left = sorted(os.listdir(os.path.join(models, "releases")))
    check("only the newest 3 releases are kept", left == ["b10", "b8", "b9"], left)
    r = client.post("/admin/model-rollback", headers=H)
    check("rollback goes to the release before", r.status_code == 200 and r.json()["current"] == "b9", r.text)
    check("current moved", os.readlink(link).endswith("b9"))
    client.post("/admin/model-rollback", headers=H)
    r = client.post("/admin/model-rollback", headers=H)
    check("nothing earlier to roll back to is a 409", r.status_code == 409, r.text)

if failures:
    print(f"\n{len(failures)} failed")
    sys.exit(1)
print("\nall admin API tests passed")
