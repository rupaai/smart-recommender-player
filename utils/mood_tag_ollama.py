"""
Mood tags from an open-weight model in Ollama (free; Wishmatch build step 6).

Same prompt, word lists and checks as utils/mood_tag.py (Haiku), so old and new tags
match; each tag records the model that wrote it (`model`).

    # Bake-off: re-tag titles Haiku already tagged and compare (writes nothing to the catalogue)
    uv run python utils/mood_tag_ollama.py bakeoff --models qwen3:8b,gemma3:12b,llama3.1:8b --n 100

    # Tag titles that have no mood tags yet, with the winner (wishmatch.tagging_model)
    uv run python utils/mood_tag_ollama.py tag [--model gemma3:12b] [--limit 500] [--min-votes 0] [--max-minutes 60]

OLLAMA_URL picks the server (default http://localhost:11434). DATABASE_PATH picks the catalogue.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sqlite3
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "packages", "wishmatch"))

from services.vibe_vocab import DIAL_NAMES  # noqa: E402
from utils.mood_tag import (  # noqa: E402
    ITEM_COLUMNS, build_system_prompt, build_user_message, normalise_tags, output_schema,
)
from wishmatch.ollama import OllamaClient  # noqa: E402

REPORT_DIR = "reports"


def db_path(arg: Optional[str]) -> str:
    return arg or os.getenv("DATABASE_PATH") or "catalogue_full.db"


def rules_model() -> str:
    try:
        import yaml
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "recommender_rules.yaml"),
                  encoding="utf-8") as f:
            return str(((yaml.safe_load(f) or {}).get("wishmatch") or {}).get("tagging_model") or "")
    except (OSError, ValueError):
        return ""


def client(model: str, timeout: float = 300.0) -> OllamaClient:
    return OllamaClient(os.getenv("OLLAMA_URL") or "http://localhost:11434", model, timeout=timeout,
                        keep_alive="10m", options={"temperature": 0, "num_predict": 900})


def tag_item(c: OllamaClient, item: Dict[str, Any], system: str, schema: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The item's mood tags from the model, checked like Haiku's, or None when the answer is unusable."""
    raw = c.generate(build_user_message(item), schema=schema, system=system)
    if not raw:
        return None
    try:
        tags = normalise_tags(json.loads(raw))
    except (ValueError, TypeError):
        return None
    if len(tags["dials"]) < len(DIAL_NAMES) // 2 or not tags["feels"]:
        return None
    tags["model"] = c.model
    tags["tagged_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return tags


def _rows(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
    conn.row_factory = sqlite3.Row
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def sample(conn: sqlite3.Connection, n: int, seed: int = 7) -> List[Dict[str, Any]]:
    """n titles Haiku tagged: 60% films and 40% shows, half well known and half less known."""
    rng = random.Random(seed)
    out: List[Dict[str, Any]] = []
    for kind, share in (("movie", 0.6), ("tv", 0.4)):
        k = round(n * share)
        for known, part in (("well", k // 2), ("somewhat", k - k // 2)):
            rows = _rows(conn, f"SELECT {ITEM_COLUMNS}, mi.vibe_tags FROM media_items mi WHERE mi.media_type = ? "
                               "AND json_extract(mi.vibe_tags, '$.known') = ? AND COALESCE(mi.overview, '') != ''",
                         (kind, known))
            out.extend(rng.sample(rows, min(part, len(rows))))
    return out


def compare(haiku: Dict[str, Any], other: Dict[str, Any]) -> Dict[str, float]:
    """How close another model's tags are to Haiku's: dial error (0..1, lower is better) and
    feels / good-for overlap (Jaccard, 0..1, higher is better)."""
    hd, od = haiku.get("dials") or {}, other.get("dials") or {}
    errs = [abs(float(hd[d]) - float(od[d])) for d in DIAL_NAMES if d in hd and d in od]

    def jac(a: List[str], b: List[str]) -> float:
        a, b = set(a or []), set(b or [])
        return len(a & b) / len(a | b) if a | b else 1.0

    return {"dial_error": sum(errs) / len(errs) if errs else 1.0,
            "feels": jac(haiku.get("feels"), other.get("feels")),
            "good_for": jac(haiku.get("good_for"), other.get("good_for")),
            "flags": jac(haiku.get("flags"), other.get("flags"))}


def bakeoff(conn: sqlite3.Connection, models: List[str], n: int, out_dir: str = REPORT_DIR) -> Dict[str, Any]:
    items = sample(conn, n)
    system, schema = build_system_prompt(), output_schema()
    results: Dict[str, Any] = {}
    for model in models:
        c = client(model)
        scores, seconds, failed = [], [], 0
        print(f"[Bake-off] {model}: {len(items)} titles", flush=True)
        for i, item in enumerate(items, 1):
            start = time.time()
            tags = tag_item(c, item, system, schema)
            seconds.append(time.time() - start)
            if tags is None:
                failed += 1
            else:
                scores.append(compare(json.loads(item["vibe_tags"]), tags))
            if i % 20 == 0:
                print(f"  {i}/{len(items)} done", flush=True)
        avg = {k: round(sum(s[k] for s in scores) / len(scores), 3) for k in scores[0]} if scores else {}
        # One number to rank by: overlap on feelings and occasions, minus dial error.
        overall = round(avg.get("feels", 0) * 0.4 + avg.get("good_for", 0) * 0.2 + avg.get("flags", 0) * 0.1
                        + (1 - avg.get("dial_error", 1)) * 0.3, 3) if avg else 0.0
        results[model] = {**avg, "overall": overall, "failed": failed,
                          "seconds_per_title": round(sum(seconds) / len(seconds), 1)}
        print(f"  {model}: {results[model]}", flush=True)
    winner = max(results, key=lambda m: (results[m]["failed"] < 0.1 * len(items), results[m]["overall"]))
    report = {"date": datetime.now(timezone.utc).isoformat(timespec="seconds"), "titles": len(items),
              "results": results, "winner": winner}
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "mood_tag_bakeoff.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    print(f"[Bake-off] winner: {winner}; report in {path}", flush=True)
    return report


def tag_new(conn: sqlite3.Connection, model: str, limit: int = 0, min_votes: int = 0, max_minutes: float = 0) -> int:
    """Tags titles that have no mood tags yet, most-voted first, stopping after `max_minutes`
    (0 = no limit; about 45 s a title on the Mac). Returns how many were tagged."""
    sql = (f"SELECT {ITEM_COLUMNS} FROM media_items mi WHERE mi.vibe_tags IS NULL AND COALESCE(mi.overview, '') != '' "
           "AND COALESCE(mi.vote_count, 0) >= ? ORDER BY mi.vote_count DESC")
    if limit:
        sql += f" LIMIT {int(limit)}"
    items = _rows(conn, sql, (min_votes,))
    if not items:
        print("[MoodTag] Every title already has mood tags.", flush=True)
        return 0
    c = client(model)
    if not c.status().get("has_model"):
        print(f"[MoodTag] Ollama has no {model}; run `ollama pull {model}` first.", flush=True)
        return 0
    system, schema = build_system_prompt(), output_schema()
    done = 0
    deadline = time.time() + max_minutes * 60 if max_minutes else None
    for i, item in enumerate(items, 1):
        if deadline and time.time() > deadline:
            print(f"[MoodTag] Time limit reached; {len(items) - i + 1} titles left for next time.", flush=True)
            break
        tags = tag_item(c, item, system, schema)
        if tags is not None:
            conn.execute("UPDATE media_items SET vibe_tags = ?, vibe_tagged_at = CURRENT_TIMESTAMP WHERE id = ?",
                         (json.dumps(tags, ensure_ascii=False), item["id"]))
            done += 1
        if i % 25 == 0:
            conn.commit()
            print(f"[MoodTag] {i}/{len(items)} ({done} tagged)", flush=True)
    conn.commit()
    print(f"[MoodTag] {done} of {len(items)} titles tagged with {model} (free).", flush=True)
    return done


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("bakeoff")
    b.add_argument("--models", required=True, help="comma-separated Ollama model names")
    b.add_argument("--n", type=int, default=100)
    t = sub.add_parser("tag")
    t.add_argument("--model", default=None, help="default: wishmatch.tagging_model in recommender_rules.yaml")
    t.add_argument("--limit", type=int, default=0)
    t.add_argument("--min-votes", type=int, default=0)
    t.add_argument("--max-minutes", type=float, default=0, help="stop after this long (0 = no limit)")
    a = ap.parse_args(argv)
    conn = sqlite3.connect(db_path(a.db))
    try:
        if a.cmd == "bakeoff":
            bakeoff(conn, [m.strip() for m in a.models.split(",") if m.strip()], a.n)
        else:
            model = a.model or rules_model()
            if not model:
                print("[MoodTag] No model: set wishmatch.tagging_model or pass --model.", flush=True)
                return 1
            tag_new(conn, model, a.limit, a.min_votes, a.max_minutes)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
