"""
Compare the legacy recommender with Wishmatch on 30 fixed requests (utils/compare_engines.py)

Runs every request through both engines on a temp COPY of the catalogue (no network, no Ollama,
no paid calls), checks the top results against simple expectations, writes a JSON file and prints
a summary.

    uv run python utils/compare_engines.py --db catalogue_full.db --out-json /tmp/compare_engines.json

Request fields: id, query, mood_id (used when query is empty), media_type ('movie' | 'tv' | None = any;
legacy cannot mix, so it runs as 'movie'), profile, duration, era, and the expectations:
  genres_all   each of top N has one of these genres        genres_none  none of top N has these
  origin       {"codes": [...], "langs": [...]} each of top N matches one
  mix          top 20 holds both films and shows (Wishmatch only; n/a for legacy)
  min_votes_median  median vote_count of top N >= X (default 1000 films / 300 shows; 0 = off)
  foreign_top  non-English titles in top N need rating >= 7.8 (default on; allow_foreign=True turns it off)
  seen         none of the viewer's seen titles in top N (together: seen by either person) (default on)
  runtime_max  each of top N has runtime under this many minutes
  years        (lo, hi) release_year range for each of top N
"""

import argparse
import json
import os
import shutil
import statistics
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.pop("OLLAMA_URL", None)   # Wishmatch word lookup only: deterministic and fast

from database import DatabaseManager  # noqa: E402
from services.recommender_service import MOOD_MAPPINGS, RecommenderService  # noqa: E402
from services.wishmatch_movies import WishmatchMovies  # noqa: E402

TOP_N = 10
MIX_N = 20
FOREIGN_MIN_RATING = 7.8
PERSON_IDS = {"me": [1], "partner": [2], "together": [1, 2]}

REQUESTS = [
    {"id": "laugh", "query": "make me laugh", "media_type": "movie", "profile": "me", "genres_all": ["Comedy"]},
    {"id": "rough_day_laugh", "query": "had a rough day, want something that makes us laugh but nothing stupid",
     "media_type": "movie", "profile": "together", "genres_all": ["Comedy"]},
    {"id": "rough_day_comfort", "query": "rough day, something comforting", "media_type": None, "profile": "together",
     "mix": True},
    {"id": "date_night", "query": "date night", "media_type": "movie", "profile": "together"},
    {"id": "cozy", "query": "something cozy", "media_type": None, "profile": "me", "mix": True},
    {"id": "scary", "query": "something scary", "media_type": "movie", "profile": "me", "genres_all": ["Horror", "Thriller"]},
    {"id": "scary_no_gore", "query": "scary but no horror", "media_type": "movie", "profile": "me",
     "genres_none": ["Horror"]},
    {"id": "korean_thriller", "query": "korean thriller", "media_type": "movie", "profile": "me",
     "genres_all": ["Thriller"], "origin": {"codes": ["KR"], "langs": ["ko"]}, "allow_foreign": True},
    {"id": "japanese_anime", "query": "japanese anime", "media_type": None, "profile": "me",
     "genres_all": ["Animation"], "origin": {"codes": ["JP"], "langs": ["ja"]}, "allow_foreign": True},
    {"id": "bollywood", "query": "bollywood", "media_type": "movie", "profile": "partner",
     "origin": {"codes": ["IN"], "langs": ["hi"]}, "allow_foreign": True},
    {"id": "mind_bending", "query": "something mind-bending", "media_type": "movie", "profile": "me"},
    {"id": "slow_beautiful", "query": "a slow beautiful film", "media_type": "movie", "profile": "partner"},
    {"id": "talk_after", "query": "something to talk about after", "media_type": "movie", "profile": "together"},
    {"id": "background_cooking", "query": "background while cooking", "media_type": None, "profile": "me", "mix": True},
    {"id": "binge_show", "query": "binge-worthy show", "media_type": "tv", "profile": "together"},
    {"id": "short_tonight", "query": "a short film for tonight", "media_type": "movie", "profile": "me",
     "duration": "short", "runtime_max": 105},
    {"id": "eighties", "query": "80s", "media_type": "movie", "profile": "me", "years": (1980, 1989)},
    {"id": "true_story", "query": "true story", "media_type": "movie", "profile": "partner"},
    {"id": "feel_good", "query": "feel-good", "media_type": None, "profile": "together", "mix": True},
    {"id": "tearjerker", "query": "tearjerker", "media_type": "movie", "profile": "me", "genres_all": ["Drama", "Romance"]},
    {"id": "no_animation", "query": "nothing animated, a fun family night", "media_type": "movie", "profile": "together",
     "genres_none": ["Animation"]},
    {"id": "hidden_gem_scifi", "query": "hidden gem sci-fi", "media_type": "movie", "profile": "me",
     "genres_all": ["Science Fiction"], "min_votes_median": 0},
    {"id": "christmas", "query": "christmas cozy winter", "media_type": None, "profile": "together", "mix": True},
    {"id": "action", "query": "action", "media_type": "movie", "profile": "partner", "genres_all": ["Action"]},
    {"id": "crime_show", "query": "crime show", "media_type": "tv", "profile": "me", "genres_all": ["Crime"]},
    {"id": "documentary", "query": "documentary", "media_type": None, "profile": "me", "genres_all": ["Documentary"]},
    {"id": "kids_family", "query": "kids family movie night", "media_type": "movie", "profile": "together",
     "genres_all": ["Family", "Animation"]},
    {"id": "mood_feel_good", "query": "", "mood_id": 1, "media_type": "movie", "profile": "together"},
    {"id": "mood_thrilling", "query": "", "mood_id": 3, "media_type": None, "profile": "me", "mix": True},
    {"id": "mood_surprise", "query": "", "mood_id": 8, "media_type": None, "profile": "partner", "mix": True},
]


# --- checks ----------------------------------------------------------------------
def _has(item, field, words):
    value = (item.get(field) or "").lower()
    return any(w.lower() in value for w in words)


def _result(name, passed, detail=""):
    return {"check": name, "passed": passed, "detail": detail}


def check_genres_all(items, words):
    bad = [i["title"] for i in items if not _has(i, "genres", words)]
    return _result("genres_all", not bad, f"missing {'/'.join(words)}: {bad[:5]}" if bad else "")


def check_genres_none(items, words):
    bad = [i["title"] for i in items if _has(i, "genres", words)]
    return _result("genres_none", not bad, f"has {'/'.join(words)}: {bad[:5]}" if bad else "")


def check_origin(items, origin):
    codes, langs = origin.get("codes") or [], origin.get("langs") or []
    bad = [i["title"] for i in items
           if not (_has(i, "origin_country", codes) or (i.get("original_language") or "") in langs)]
    return _result("origin", not bad, f"wrong origin: {bad[:5]}" if bad else "")


def check_mix(items):
    kinds = {i.get("media_type") for i in items[:MIX_N]}
    return _result("mix", {"movie", "tv"} <= kinds, f"kinds in top {MIX_N}: {sorted(k for k in kinds if k)}")


def check_min_votes_median(items, floor):
    votes = [i.get("vote_count") or 0 for i in items]
    median = statistics.median(votes) if votes else 0
    return _result("min_votes_median", bool(votes) and median >= floor, f"median {median:g}, need {floor}")


def check_foreign_top(items, min_rating=FOREIGN_MIN_RATING):
    bad = [f"{i['title']} ({i.get('original_language')}, {i.get('rating')})" for i in items
           if (i.get("original_language") or "en") != "en" and (i.get("rating") or 0) < min_rating]
    return _result("foreign_top", not bad, f"weak non-English: {bad[:5]}" if bad else "")


def check_seen(items, seen_ids):
    bad = [i["title"] for i in items if i.get("id") in seen_ids]
    return _result("seen", not bad, f"already seen: {bad[:5]}" if bad else "")


def check_runtime_max(items, limit):
    bad = [i["title"] for i in items if not 0 < (i.get("runtime") or 0) < limit]
    return _result("runtime_max", not bad, f"runtime >= {limit} or unknown: {bad[:5]}" if bad else "")


def check_years(items, years):
    lo, hi = years
    bad = [f"{i['title']} ({i.get('release_year')})" for i in items if not lo <= (i.get("release_year") or 0) <= hi]
    return _result("years", not bad, f"outside {lo}-{hi}: {bad[:5]}" if bad else "")


def run_checks(req, items, seen_by_person, engine, top_n=TOP_N):
    """All checks for one request on one engine. `items` is the engine's result list (up to 20)."""
    top = items[:top_n]
    out = []
    if not top:
        return [_result("has_results", False, "engine returned nothing")]
    if req.get("genres_all"):
        out.append(check_genres_all(top, req["genres_all"]))
    if req.get("genres_none"):
        out.append(check_genres_none(top, req["genres_none"]))
    if req.get("origin"):
        out.append(check_origin(top, req["origin"]))
    if req.get("mix"):
        if engine == "legacy":
            out.append({"check": "mix", "passed": None, "detail": "n/a (legacy cannot mix)"})
        else:
            out.append(check_mix(items))
    if req.get("years"):
        out.append(check_years(top, req["years"]))
    if req.get("runtime_max"):
        out.append(check_runtime_max(top, req["runtime_max"]))
    floor = req.get("min_votes_median")
    if floor is None:
        floor = 300 if req.get("media_type") == "tv" else 1000
    if floor:
        out.append(check_min_votes_median(top, floor))
    if not req.get("allow_foreign"):
        out.append(check_foreign_top(top))
    if req.get("seen", True):
        ids = set()
        for person in PERSON_IDS[req.get("profile", "together")]:
            ids |= seen_by_person.get(person, set())
        out.append(check_seen(top, ids))
    return out


# --- running ----------------------------------------------------------------------
def seen_titles(db):
    with db.get_connection() as conn:
        rows = conn.execute("SELECT profile_id, media_item_id FROM watch_history "
                            "WHERE COALESCE(status, 'seen') = 'seen'").fetchall()
    seen = {}
    for r in rows:
        seen.setdefault(r[0], set()).add(r[1])
    return seen


def mood_genres(mood_id):
    return (MOOD_MAPPINGS.get(mood_id, MOOD_MAPPINGS[8])["genres"]) or None


def kwargs_for(req, engine):
    """The same kwargs Discover sends (server._recommend_kwargs), minus session bookkeeping."""
    media_type = req.get("media_type")
    if engine == "legacy" and media_type is None:
        media_type = "movie"
    mood_id = req.get("mood_id", 8)
    return dict(
        mood_id=mood_id, exclude_watched=True, era_preference=req.get("era"),
        duration_preference=req.get("duration"), page=1, page_size=MIX_N, query=req.get("query") or None,
        media_type=media_type, genres_override=mood_genres(mood_id), profile=req.get("profile", "together"),
        variety=True)


def slim(item, wishmatch):
    row = {"title": item.get("title"), "year": item.get("release_year"), "media_type": item.get("media_type"),
           "genres": item.get("genres"), "rating": item.get("rating"), "vote_count": item.get("vote_count"),
           "original_language": item.get("original_language")}
    if wishmatch:
        row["taste_reasons"] = item.get("taste_reasons")
    return row


FIELDS = ("origin_country", "original_language", "runtime", "vote_count", "release_year", "rating", "genres")


def load_facts(db):
    """id -> catalogue facts. Wishmatch rows do not carry every field Discover cards use
    (e.g. original_language), so the checks read them from the catalogue for both engines."""
    with db.get_connection() as conn:
        rows = conn.execute(f"SELECT id, {', '.join(FIELDS)} FROM media_items").fetchall()
    return {r[0]: dict(zip(FIELDS, r[1:])) for r in rows}


def enrich(items, facts):
    return [{**i, **{k: v for k, v in facts.get(i.get("id"), {}).items() if i.get(k) is None}} for i in items]


def run_requests(requests, legacy, wishmatch, seen_by_person, top_n=TOP_N, facts=None):
    """Returns (results list, seconds per engine)."""
    engines = {"legacy": legacy, "wishmatch": wishmatch}
    timing = {"legacy": 0.0, "wishmatch": 0.0}
    results = []
    for req in requests:
        entry = {"id": req["id"], "query": req.get("query"),
                 "settings": {k: v for k, v in req.items() if k not in ("id", "query")}, "engines": {}}
        for name, engine in engines.items():
            started = time.time()
            try:
                out = engine.recommend(**kwargs_for(req, name))
                items = enrich(out.get("items") or [], facts or {})
                error = None
            except Exception as exc:  # noqa: BLE001
                items, error = [], f"{type(exc).__name__}: {exc}"
            timing[name] += time.time() - started
            checks = run_checks(req, items, seen_by_person, name, top_n)
            if error:
                checks = [_result("has_results", False, error)]
            entry["engines"][name] = {"top": [slim(i, name == "wishmatch") for i in items[:top_n]], "checks": checks}
        results.append(entry)
    return results, timing


def summarise(results, timing):
    lines = []
    totals = {}
    for name in ("legacy", "wishmatch"):
        scored = [(r["id"], c) for r in results for c in r["engines"][name]["checks"] if c["passed"] is not None]
        passed = sum(1 for _, c in scored if c["passed"])
        totals[name] = (passed, len(scored))
        lines.append(f"{name}: {passed}/{len(scored)} checks passed in {timing[name]:.1f}s")
        for rid, c in scored:
            if not c["passed"]:
                lines.append(f"    FAIL {rid} / {c['check']}: {c['detail']}")
    lines.append("Requests where one engine passed and the other failed:")
    split = 0
    for r in results:
        a, b = (r["engines"][n]["checks"] for n in ("legacy", "wishmatch"))
        bymap = {n: {c["check"]: c["passed"] for c in cs} for n, cs in (("legacy", a), ("wishmatch", b))}
        for check_name in bymap["legacy"]:
            lp, wp = bymap["legacy"][check_name], bymap["wishmatch"].get(check_name)
            if lp is None or wp is None or lp == wp:
                continue
            split += 1
            lines.append(f"    {r['id']} / {check_name}: legacy {'pass' if lp else 'FAIL'}, "
                         f"wishmatch {'pass' if wp else 'FAIL'}")
    if not split:
        lines.append("    none")
    return "\n".join(lines)


def build_engines(db):
    legacy = RecommenderService(db, shortlist_size=500, final_pool=200)
    wishmatch = WishmatchMovies(db, MOOD_MAPPINGS)
    return legacy, wishmatch


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("--db", default="catalogue_full.db")
    parser.add_argument("--out-json", default="/tmp/compare_engines.json")
    args = parser.parse_args()

    copy = os.path.join(tempfile.mkdtemp(prefix="compare_engines_"), "catalogue.db")
    shutil.copyfile(args.db, copy)
    db = DatabaseManager(copy)
    legacy, wishmatch = build_engines(db)
    results, timing = run_requests(REQUESTS, legacy, wishmatch, seen_titles(db), facts=load_facts(db))
    with open(args.out_json, "w") as fh:
        json.dump({"top_n": TOP_N, "requests": results}, fh, indent=2, default=str)
    print(summarise(results, timing))
    print(f"\nJSON written to {args.out_json}")


if __name__ == "__main__":
    main()
