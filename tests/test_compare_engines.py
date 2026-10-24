"""
Engine comparison script tests (tests/test_compare_engines.py)

Checks that the checks in utils/compare_engines.py work (genres, mix, seen, origin, votes, foreign rule),
and that running a few requests end to end on a small catalogue gives the JSON shape.
The 30 real requests are not run here.

    uv run python tests/test_compare_engines.py
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_wishmatch_movies import catalogue, ids_by_title, rate  # noqa: E402

from utils import compare_engines as ce  # noqa: E402

failures = []


def check(label, condition, detail=""):
    if condition:
        print(f"  ✅ {label}")
    else:
        print(f"  ❌ {label} — got: {detail}")
        failures.append(label)


def item(title, genres="Comedy", kind="movie", votes=5000, rating=7.5, lang="en", country="US", id_=None):
    return {"id": id_, "title": title, "genres": genres, "media_type": kind, "vote_count": votes,
            "rating": rating, "original_language": lang, "origin_country": country}


def passed(results, name):
    return [r["passed"] for r in results if r["check"] == name]


def main():
    print("\n[1] The checks themselves")
    comedies = [item(f"C{i}") for i in range(10)]
    check("a comedy-only list passes genres_all Comedy", ce.check_genres_all(comedies, ["comedy"])["passed"])
    check("a thriller in the list fails genres_all Comedy",
          not ce.check_genres_all(comedies[:9] + [item("T", "Thriller")], ["Comedy"])["passed"])
    check("genres_none fails when a banned genre is present",
          not ce.check_genres_none([item("H", "Horror, Thriller")], ["Horror"])["passed"])
    check("genres_none passes when absent", ce.check_genres_none(comedies, ["Horror"])["passed"])
    films_and_shows = [item("F"), item("S", kind="tv")]
    check("mix passes with films and shows", ce.check_mix(films_and_shows)["passed"])
    check("mix fails with films only", not ce.check_mix(comedies)["passed"])
    check("seen fails when a seen id is in the list", not ce.check_seen([item("X", id_=5)], {5})["passed"])
    check("seen passes when none are seen", ce.check_seen([item("X", id_=6)], {5})["passed"])
    korean = [item("K", "Thriller", lang="ko", country="KR")]
    origin = {"codes": ["KR"], "langs": ["ko"]}
    check("origin passes for a Korean title", ce.check_origin(korean, origin)["passed"])
    check("origin fails for an American title", not ce.check_origin(comedies[:1], origin)["passed"])
    check("origin matches on language alone", ce.check_origin([item("K2", lang="ko", country="")], origin)["passed"])
    check("min_votes_median fails for obscure titles",
          not ce.check_min_votes_median([item("o", votes=50)] * 5, 1000)["passed"])
    check("foreign rule fails a weak non-English title",
          not ce.check_foreign_top([item("w", lang="fr", rating=7.0)])["passed"])
    check("foreign rule passes a strong non-English title",
          ce.check_foreign_top([item("w", lang="fr", rating=8.4)])["passed"])

    print("\n[2] run_checks picks the right checks per engine")
    req = {"id": "x", "media_type": None, "profile": "together", "mix": True, "genres_all": ["Comedy"]}
    legacy_checks = ce.run_checks(req, comedies, {1: {99}}, "legacy")
    check("mix is n/a for legacy", passed(legacy_checks, "mix") == [None], legacy_checks)
    wm_checks = ce.run_checks(req, comedies, {1: {99}}, "wishmatch")
    check("mix is scored for Wishmatch", passed(wm_checks, "mix") == [False], wm_checks)
    seen_checks = ce.run_checks({"id": "y", "profile": "together"}, [item("A", id_=7)], {2: {7}}, "wishmatch")
    check("together: a title seen by either person fails", passed(seen_checks, "seen") == [False], seen_checks)
    me_checks = ce.run_checks({"id": "y", "profile": "me"}, [item("A", id_=7)], {2: {7}}, "wishmatch")
    check("me: a title only the partner saw is fine", passed(me_checks, "seen") == [True], me_checks)
    check("empty results fail loudly", passed(ce.run_checks(req, [], {}, "legacy"), "has_results") == [False])

    print("\n[3] End to end on a small catalogue")
    check("there are 30 real requests with unique ids",
          len(ce.REQUESTS) == 30 and len({r["id"] for r in ce.REQUESTS}) == 30, len(ce.REQUESTS))
    db = catalogue()
    ids = ids_by_title(db)
    rate(db, 1, ids["Funny Film 0"], 9)
    legacy, wishmatch = ce.build_engines(db)
    requests = [
        {"id": "laugh", "query": "make me laugh", "media_type": "movie", "profile": "me",
         "genres_all": ["Comedy"], "min_votes_median": 0},
        {"id": "any", "query": "make me laugh", "media_type": None, "profile": "together", "mix": True,
         "min_votes_median": 0},
        {"id": "mood", "query": "", "mood_id": 3, "media_type": "tv", "profile": "partner", "min_votes_median": 0},
    ]
    results, timing = ce.run_requests(requests, legacy, wishmatch, ce.seen_titles(db), facts=ce.load_facts(db))
    check("one result per request", [r["id"] for r in results] == ["laugh", "any", "mood"])
    first = results[0]
    check("each engine has top and checks", all({"top", "checks"} <= set(first["engines"][e])
                                               for e in ("legacy", "wishmatch")), first.keys())
    row = first["engines"]["wishmatch"]["top"][0]
    check("a top row has the listed fields", {"title", "year", "media_type", "genres", "rating", "vote_count",
                                              "original_language", "taste_reasons"} <= set(row), row)
    check("legacy rows have no taste_reasons field", "taste_reasons" not in first["engines"]["legacy"]["top"][0])
    check("top is at most 10", all(len(e["top"]) <= 10 for r in results for e in r["engines"].values()))
    check("the seen film is not in the viewer's list",
          all(t["title"] != "Funny Film 0" for e in ("legacy", "wishmatch") for t in first["engines"][e]["top"]))
    check("Wishmatch mixes films and shows for 'any'", passed(results[1]["engines"]["wishmatch"]["checks"], "mix") == [True],
          results[1]["engines"]["wishmatch"]["checks"])
    check("timing is kept per engine", set(timing) == {"legacy", "wishmatch"})
    check("results can be written as JSON", bool(json.dumps({"requests": results}, default=str)))
    summary = ce.summarise(results, timing)
    check("summary lists both engines", "legacy:" in summary and "wishmatch:" in summary, summary)

    if failures:
        print(f"\n{len(failures)} failed")
        sys.exit(1)
    print("\nAll passed")


if __name__ == "__main__":
    main()
