"""
Recommender rules tests (tests/test_recommender_rules.py)

The word lists and weights come from recommender_rules.yaml; these check what
they do end to end on a small catalogue: typed genres filter, described genres
pull, mood cards give way to what was typed, foreign-language titles need to be
top tier unless asked for.

    uv run python tests/test_recommender_rules.py
"""

import os
import sys
import tempfile
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import DatabaseManager  # noqa: E402
from services import recommender_rules  # noqa: E402
from services.nl_choose import context_for  # noqa: E402
from services.recommender_service import (  # noqa: E402
    MOOD_MAPPINGS, RecommenderService, parse_query_intent, resolve_effective_genres,
)

failures = []


def check(label, condition, detail=""):
    if condition:
        print(f"  ✅ {label}")
    else:
        print(f"  ❌ {label} — got: {detail}")
        failures.append(label)


def catalogue():
    """40 excellent English dramas, 10 good English comedies, and foreign titles
    on both sides of the gate. The dramas fill a small shortlist on quality alone."""
    rows = []
    n = 0

    def add(title, genres, rating, votes, lang="en", country="US", media_type="movie"):
        nonlocal n
        n += 1
        rows.append({"media_type": media_type, "tmdb_id": 50000 + n, "title": title, "overview": f"{title}.",
                     "genres": genres, "rating": rating, "vote_count": votes,
                     "original_language": lang, "origin_country": country})

    for i in range(40):
        add(f"Great Drama {i}", "Drama", 8.4, 20000)
    for i in range(10):
        add(f"Good Comedy {i}", "Comedy", 7.0, 8000)
    add("Okay Korean Comedy", "Comedy", 7.0, 900, lang="ko", country="KR")
    add("Top Korean Thriller", "Thriller, Drama", 8.5, 18000, lang="ko", country="KR")
    add("Okay French Drama", "Drama", 7.2, 3000, lang="fr", country="FR")
    add("Classic French Comedy", "Comedy", 7.6, 5000, lang="fr", country="FR")
    add("Animated Comedy", "Animation, Comedy, Family", 7.8, 9000)
    add("Good Show", "Comedy", 7.5, 800, media_type="tv")
    add("Scary Show", "Mystery, Drama", 7.4, 900, media_type="tv")
    path = tempfile.mktemp(suffix=".db")
    db = DatabaseManager(path)
    db.upsert_catalogue_items(rows)
    return db


def titles(out):
    return [i["title"] for i in out["items"]]


def genres_of(out):
    return [i.get("genres") or "" for i in out["items"]]


def main():
    db = catalogue()
    r = RecommenderService(db, shortlist_size=20, final_pool=60)

    print("\n[1] Typed genre names are a filter")
    out = r.recommend(query="laugh and comedy", page_size=20)
    check("'laugh and comedy' returns only comedies", out["items"] and all("Comedy" in g for g in genres_of(out)),
          genres_of(out))
    out = r.recommend(query="horror comedy", page_size=10)
    check("an empty genre filter falls back instead of returning nothing", out["items"], out)

    print("\n[2] Described genres pull hard, even past a quality cut full of dramas")
    out = r.recommend(query="something to make me laugh", page_size=10)
    check("'make me laugh' puts comedies on top", all("Comedy" in g for g in genres_of(out)[:5]), titles(out)[:5])
    spec = parse_query_intent("make me laugh")
    check("'laugh' is a hint for Comedy, not a word to search synopses for",
          spec["hinted_genres"] == ["Comedy"] and "laugh" not in spec["content_words"], spec)

    print("\n[3] What you type beats the mood card")
    thrill = MOOD_MAPPINGS[3]["genres"]
    out = r.recommend(mood_id=3, genres_override=thrill, query="comedy", page_size=10)
    check("comedy typed on the Thrilling card still gives comedies",
          out["items"] and all("Comedy" in g for g in genres_of(out)), genres_of(out))
    spec = resolve_effective_genres(thrill, None, "something funny")
    check("a described genre turns the mood's genres into a soft pull",
          spec["include"] == [] and spec["boost"].get("thriller", 0) > 0, spec)
    spec = resolve_effective_genres(thrill, None, None)
    check("with nothing typed the mood's genres still filter", spec["include"] == thrill, spec)

    print("\n[4] Foreign-language titles only when top tier")
    out = r.recommend(page_size=60, query="comedy")
    t = titles(out)
    check("an ordinary Korean comedy is left out", "Okay Korean Comedy" not in t, t)
    check("a French comedy below 7.8 is left out", "Classic French Comedy" not in t, t)
    out = r.recommend(page_size=60)
    t = titles(out)
    check("a top-tier Korean thriller is shown", "Top Korean Thriller" in t, t)
    check("an ordinary French drama is left out", "Okay French Drama" not in t, t)
    out = r.recommend(query="korean comedy", page_size=10)
    check("asking for Korean shows the ordinary Korean comedy", "Okay Korean Comedy" in titles(out), titles(out))
    out = r.recommend(query="a foreign comedy", page_size=60)
    check("'foreign' lifts the gate", "Okay Korean Comedy" in titles(out) and "Classic French Comedy" in titles(out),
          titles(out))
    french = [i["id"] for i in db.get_top_candidates(limit=500) if i["title"] == "Okay French Drama"]
    out = r.recommend(page_size=60, only_ids=french)
    check("the library row (only_ids) is not gated", "Okay French Drama" in titles(out), titles(out))
    gate = recommender_rules.foreign_gate(recommender_rules.RULES, "tv")
    check("shows have their own vote threshold", gate["min_votes"] < recommender_rules.foreign_gate(
        recommender_rules.RULES, "movie")["min_votes"], gate)

    print("\n[5] Soft wishes")
    spec = parse_query_intent("funny, less animation")
    check("'less animation' pushes down, never excludes",
          spec["boost_weights"].get("Animation", 0) < 0 and "Animation" not in spec["exclude_genres"], spec)
    spec = parse_query_intent("relaxing horror")
    check("a tone never excludes a genre the request named", "Horror" in spec["include_genres"]
          and "Horror" not in spec["exclude_genres"], spec)
    out = r.recommend(media_type="tv", query="something scary", page_size=10)
    check("TV with a hint TMDB's TV genres lack still returns shows", out["items"], out)

    print("\n[7] The rules file")
    check("rules load from recommender_rules.yaml", recommender_rules.RULES["genre_names"].get("Comedy"))
    broken = recommender_rules.load_rules(os.path.join(tempfile.mkdtemp(), "missing.yaml"))
    check("a missing rules file falls back instead of failing", broken["weights"]["min_rating"] == 6.0, broken)
    moods = {k: v for k, v in MOOD_MAPPINGS.items()}
    table = [
        (datetime(2026, 10, 19, 14), 1, None),      # Monday afternoon
        (datetime(2026, 10, 23, 20), 8, None),      # Friday evening
        (datetime(2026, 10, 25, 20), 7, "short"),   # Sunday evening
        (datetime(2026, 10, 20, 20), 8, "short"),   # Tuesday evening
        (datetime(2026, 10, 20, 23), 7, "short"),   # Tuesday night
        (datetime(2026, 10, 24, 1), 8, None),       # early Saturday = Friday night
    ]
    got = [(context_for(t, moods)["mood_id"], context_for(t, moods)["duration"]) for t, _, _ in table]
    check("Choose for us defaults come from choose_defaults",
          got == [(m, d) for _, m, d in table], got)

    if failures:
        print(f"\n❌ {len(failures)} FAILED: {failures}")
        sys.exit(1)
    print("\nAll recommender rules tests passed")


if __name__ == "__main__":
    main()
