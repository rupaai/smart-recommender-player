"""
Times Wishmatch searches on a running server, so a speed change can be measured before and after.

    uv run python utils/time_wishmatch.py http://192.168.1.183:7101
    uv run python utils/time_wishmatch.py            # uses CC_SERVER from ~/.couchcinema/env

What it measures (all free, nothing is downloaded):
- known wish: words the word lookup knows ("funny", "not scary"); should never wait for the model
- new wish: words the lookup doesn't know, with a time stamp so the model has never seen it.
  This is the slow case: the model on mars's processor has to read it
- same wish again: should be instant (the reading is remembered)
- question: a Discover search with questions on; includes wording the first question

Wishes are read through /api/rec/movies/wish (nothing is logged). The question search goes through
/api/discover/start, which records one Discover search like any other; it is left unanswered,
so no "shown titles" are logged for training. No admin token is needed or read.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from typing import Any, Dict, List, Tuple

KNOWN = ["something funny and cosy", "not scary, make me laugh"]
NEW = ["a film that feels like a warm blanket on a rainy {stamp}",
       "something like a lighthouse keeper's quiet {stamp}"]
QUESTION_WISHES = ["something to watch tonight", "a gripping story"]


def server_from_env() -> str:
    try:
        with open(os.path.expanduser("~/.couchcinema/env"), encoding="utf-8") as f:
            for line in f:
                key, _, value = line.strip().partition("=")
                if key.replace("export ", "").strip() == "CC_SERVER":
                    return value.strip().strip('"').strip("'")
    except OSError:
        pass
    return "http://localhost:8000"


def call(base: str, path: str, body: Dict[str, Any] = None, timeout: float = 120) -> Tuple[float, Dict[str, Any]]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base.rstrip("/") + path, data=data, method="POST" if data else "GET",
                                 headers={"Content-Type": "application/json"})
    start = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        out = json.loads(resp.read().decode() or "{}")
    return round(time.time() - start, 2), out


def run(base: str) -> List[Dict[str, Any]]:
    stamp = time.strftime("%H%M%S")
    rows: List[Dict[str, Any]] = []
    _, status = call(base, "/api/rec/status")
    movies = status.get("movies") or {}
    print("wish reader:", json.dumps(movies.get("wish_reader")), "| warm-up:", json.dumps(movies.get("warmup")))
    for text in KNOWN:
        secs, out = call(base, "/api/rec/movies/wish", {"text": text})
        rows.append({"case": "known wish", "text": text, "seconds": secs, "source": out.get("source")})
    for text in (t.format(stamp=stamp) for t in NEW):
        secs, out = call(base, "/api/rec/movies/wish", {"text": text})
        rows.append({"case": "new wish", "text": text, "seconds": secs, "source": out.get("source")})
        secs, out = call(base, "/api/rec/movies/wish", {"text": text})
        rows.append({"case": "same wish again", "text": text, "seconds": secs, "source": out.get("source")})
    for text in QUESTION_WISHES:
        secs, out = call(base, "/api/discover/start", {"query": text, "ask": True, "profile": "together",
                                                       "media_type": "movie", "page_size": 10})
        q = out.get("question") or {}
        rows.append({"case": "question", "text": text, "seconds": secs, "source": q.get("text") or "no question"})
    return rows


def main() -> None:
    base = sys.argv[1] if len(sys.argv) > 1 else server_from_env()
    print(f"Timing Wishmatch on {base}")
    rows = run(base)
    width = max(len(r["text"]) for r in rows)
    for r in rows:
        print(f"{r['case']:<16} {r['seconds']:>6.2f} s  {r['text']:<{width}}  ({r['source']})")
    _, status = call(base, "/api/rec/status")
    reader = (status.get("movies") or {}).get("wish_reader") or {}
    print("model's last answer took:", reader.get("last_seconds"), "s", "| where the time went:", json.dumps(reader.get("last_timing")))


if __name__ == "__main__":
    main()
