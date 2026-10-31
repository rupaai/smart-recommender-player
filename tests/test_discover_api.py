"""
API tests for the mood-map questions endpoints (/api/discover/start, /answer, /finish)

TestClient on server.py with a throwaway DB holding a synthetic catalogue (genres, runtime,
vote_count, some facets / vibe_tags, vibe_cluster_id).

    uv run python tests/test_discover_api.py
"""

import json
import os
import random
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

failures = []


def check(label, condition, detail=""):
    if condition:
        print(f"  ✅ {label}")
    else:
        print(f"  ❌ {label} — got: {detail}")
        failures.append(label)


GENRES = [("Thriller, Action", 53), ("Romance, Comedy", 10749), ("Drama", 18), ("Comedy", 35),
          ("Family, Animation", 16), ("Horror", 27), ("Science Fiction, Adventure", 878), ("Documentary", 99)]


def seed(db_file, n=250):
    rnd = random.Random(7)
    conn = sqlite3.connect(db_file)
    for i in range(1, n + 1):
        genres, gid = GENRES[i % len(GENRES)]
        facets = None
        if i % 3:
            facets = json.dumps({d: round(rnd.random(), 2) for d in
                                 ("tension", "energy", "pace", "darkness", "humour", "weight", "ending_valence")})
        tags = json.dumps({"tags": ["cosy"] if i % 5 == 0 else ["bleak"]}) if i % 4 == 0 else None
        conn.execute(
            "INSERT INTO media_items (media_type, tmdb_id, title, release_year, overview, genres, genre_ids, rating,"
            " vote_count, runtime, facets, vibe_tags, vibe_cluster_id) VALUES ('movie', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (i, f"Synthetic Film {i}", 1980 + i % 44, f"A film number {i}.", genres, str(gid),
             round(6.2 + rnd.random() * 2.5, 1), 800 + i * 20, 80 + i % 70, facets, tags, i % 6))
    conn.commit()
    conn.close()


with tempfile.TemporaryDirectory() as tmp:
    cfg_file = os.path.join(tmp, "config.yaml")
    with open(cfg_file, "w", encoding="utf-8") as f:
        json.dump({
            "media_folders": [os.path.join(tmp, "Movies"), os.path.join(tmp, "Shows")],
            "jellyfin_library": {"movie_dir": os.path.join(tmp, "Movies"), "tv_dir": os.path.join(tmp, "Shows")},
            "torrent": {"download_dir": os.path.join(tmp, "downloads")},
        }, f)  # JSON is valid YAML
    os.environ["CONFIG_PATH"] = cfg_file
    os.environ["DATABASE_PATH"] = os.path.join(tmp, "c.db")
    os.environ["MODELS_DIR"] = os.path.join(tmp, "models")
    import server  # noqa: E402 - needs the env set first
    from web import state  # noqa: E402
    from fastapi.testclient import TestClient

    seed(os.environ["DATABASE_PATH"])
    client = TestClient(server.app)
    body = {"media_type": "movie", "page_size": 10, "query": "something tonight", "ask": True}

    print("start")
    r = client.post("/api/discover/start", json=body)
    d = r.json()
    check("200", r.status_code == 200, r.text)
    check("results returned", len(d.get("items") or []) > 0, d.keys())
    q = d.get("question") or {}
    check("first question is A3", q.get("id") == "A3", q)
    check("options have no examples", all(not o.get("examples") for o in q.get("options", [])), q)
    moods = d.get("moods") or []
    check("moods: top 3 with id, label, p", len(moods) == 3 and all(set(m) == {"id", "label", "p"} for m in moods), moods)
    check("p rounded to 2 decimals", all(round(m["p"], 2) == m["p"] for m in moods), moods)
    check("question_skipped is None", d.get("question_skipped") is None, d.get("question_skipped"))
    sid = d["session_id"]

    print("ask off")
    r = client.post("/api/discover/start", json={**body, "ask": False})
    check("no question when ask is false", r.status_code == 200 and r.json().get("question") is None, r.text[:200])

    print("answer")
    seen = ["A3"]
    steps = 0
    while d.get("question") and steps < 10:
        q = d["question"]
        opt = q["options"][0]["id"] if steps % 2 == 0 else "skip"
        r = client.post("/api/discover/answer", json={"session_id": sid, "question_id": q["id"], "option_id": opt})
        check(f"answer {q['id']} ok", r.status_code == 200, r.text[:200])
        d = r.json()
        check("moods after answer", len(d.get("moods") or []) == 3, d.get("moods"))
        if d.get("question"):
            seen.append(d["question"]["id"])
        steps += 1
    check("session ends within 6 answers", d.get("question") is None and 2 <= steps <= 6, (steps, seen))
    check("no question repeated", len(seen) == len(set(seen)), seen)
    check("results still present when done", len(d.get("items") or []) > 0)
    with state.db.get_connection() as conn:
        row = conn.execute("SELECT belief_json, asked_order FROM discover_sessions WHERE id = ?", (sid,)).fetchone()
        imps = conn.execute("SELECT COUNT(*) FROM discover_impressions WHERE session_id = ?", (sid,)).fetchone()[0]
    check("belief logged over the moods", row and len(json.loads(row[0])) == 30, row)
    check("asked order logged", row and json.loads(row[1])[0] == "A3", row)
    check("impressions logged when done", imps > 0, imps)
    r = client.post("/api/discover/answer", json={"session_id": sid, "question_id": "A3", "option_id": "skip"})
    check("answering a closed session is a 400", r.status_code == 400, r.status_code)

    print("finish")
    d = client.post("/api/discover/start", json=body).json()
    sid2 = d["session_id"]
    r = client.post("/api/discover/finish", json={"session_id": sid2})
    f = r.json()
    check("finish 200 with no question", r.status_code == 200 and f.get("question") is None, r.text[:200])
    check("finish keeps moods and items", len(f.get("moods") or []) == 3 and len(f.get("items") or []) > 0)
    check("unknown session is a 404", client.post("/api/discover/finish", json={"session_id": 999999}).status_code == 404)

    print("paging after the session")
    r = client.post("/api/recommend", json={**body, "page": 2, "page_size": 5, "session_id": sid2})
    check("/api/recommend pages the answered pool", r.status_code == 200 and len(r.json().get("items") or []) > 0, r.text[:200])

    print("empty pool")
    r = client.post("/api/discover/start", json={**body, "genres": ["Western"], "min_rating": 9.9})
    e = r.json()
    check("no question and no_results", e.get("question") is None and e.get("question_skipped") == "no_results", e.get("question_skipped"))

print()
if failures:
    print(f"{len(failures)} FAILED: {failures}")
    sys.exit(1)
print("all checks passed")
