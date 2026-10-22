"""
Wishmatch movie adapter tests (tests/test_wishmatch_movies.py)

On a small tagged catalogue: films and shows both appear when no type is chosen,
the wish moves the order ("make me laugh" -> funny titles), typed genres and
places filter, the foreign-title rule holds, little-known titles stay out unless
asked for, seen titles drop out, each person's taste moves the order, and the
engine switch picks the right engine and falls back to legacy when Wishmatch fails.
Also checks that the wishmatch: section of recommender_rules.yaml only uses
words from services/vibe_vocab.py.

    uv run python tests/test_wishmatch_movies.py
"""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import DatabaseManager  # noqa: E402
from services import vibe_vocab  # noqa: E402
from services.generative_retriever import route_recommend  # noqa: E402
from services.recommender_service import MOOD_MAPPINGS, RecommenderService  # noqa: E402
from services.wishmatch_movies import EngineSwitch, WishmatchMovies  # noqa: E402
from services.wishmatch_movies.adapter import load_wishmatch_rules  # noqa: E402
from services.wishmatch_movies.router import engine_name  # noqa: E402

failures = []


def check(label, condition, detail=""):
    if condition:
        print(f"  ✅ {label}")
    else:
        print(f"  ❌ {label} — got: {detail}")
        failures.append(label)


def dials(**kw):
    base = {d: 0.4 for d in vibe_vocab.DIAL_NAMES}
    base.update(kw)
    return base


FUNNY = dict(humour=0.9, energy=0.4, tension=0.1, darkness=0.1)
TENSE = dict(humour=0.1, energy=0.8, tension=0.9, darkness=0.7)


def catalogue():
    """12 funny films, 12 tense films, 8 funny shows, 6 tense shows, foreign and little-known extras."""
    rows, tags = [], {}
    n = 0

    def add(title, genres, kind="movie", votes=5000, rating=7.5, lang="en", country="US",
            known="well", traits=None, feels=(), flags=()):
        nonlocal n
        n += 1
        rows.append({"media_type": kind, "tmdb_id": 70000 + n, "title": title, "overview": f"{title}.",
                     "genres": genres, "rating": rating, "vote_count": votes,
                     "original_language": lang, "origin_country": country})
        tags[(kind, 70000 + n)] = {"known": known, "dials": dials(**(traits or {})), "feels": list(feels),
                                   "good_for": [], "setting": [], "season_weather": [], "flags": list(flags),
                                   "vibe": f"{title} in one line."}

    for i in range(12):
        add(f"Funny Film {i}", "Comedy", votes=9000 - i, traits=FUNNY, feels=("silly", "witty"))
    for i in range(12):
        add(f"Tense Film {i}", "Thriller", votes=9500 - i, traits=TENSE, feels=("tense",), flags=("animal_harm",))
    for i in range(8):
        add(f"Funny Show {i}", "Comedy", kind="tv", votes=3000 - i, traits=FUNNY, feels=("playful",))
    for i in range(6):
        add(f"Tense Show {i}", "Crime, Drama", kind="tv", votes=3200 - i, traits=TENSE, feels=("tense",))
    add("Horror Film", "Horror", votes=8000, traits=TENSE, feels=("dread",))
    add("Ordinary Korean Thriller", "Thriller", votes=900, rating=7.0, lang="ko", country="KR", traits=TENSE)
    add("Top Korean Thriller", "Thriller, Drama", votes=18000, rating=8.5, lang="ko", country="KR", traits=TENSE)
    add("Obscure Funny Film", "Comedy", votes=40, known="not", traits=FUNNY, feels=("silly",))

    path = tempfile.mktemp(suffix=".db")
    db = DatabaseManager(path)
    db.upsert_catalogue_items(rows)
    with db.get_connection() as conn:
        for (kind, tmdb), t in tags.items():
            conn.execute("UPDATE media_items SET vibe_tags = ?, vibe_tagged_at = '2026-10-08' "
                         "WHERE media_type = ? AND tmdb_id = ?", (json.dumps(t), kind, tmdb))
    return db


def ids_by_title(db):
    with db.get_connection() as conn:
        return {r["title"]: r["id"] for r in conn.execute("SELECT id, title FROM media_items")}


def rate(db, profile, item_id, rating, status="seen"):
    with db.get_connection() as conn:
        conn.execute("INSERT INTO watch_history (media_item_id, profile_id, user_rating, status) VALUES (?, ?, ?, ?)",
                     (item_id, profile, rating, status))


def titles(out):
    return [i["title"] for i in out["items"]]


def main():
    db = catalogue()
    ids = ids_by_title(db)
    wm = WishmatchMovies(db, MOOD_MAPPINGS)

    print("\n[1] Films and shows both appear when no type is chosen")
    out = wm.recommend(media_type=None, page_size=20)
    kinds = {i["media_type"] for i in out["items"]}
    check("top 20 with no type has films and shows", kinds == {"movie", "tv"}, kinds)
    out = wm.recommend(query="make me laugh", media_type=None, page_size=20)
    kinds = [i["media_type"] for i in out["items"]]
    check("a typed wish with no type also mixes them", "tv" in kinds and "movie" in kinds, kinds)
    check("about one pick in three is a show", 4 <= kinds[:12].count("tv") <= 5, kinds[:12])
    out = wm.recommend(media_type="tv", page_size=20)
    check("shows only gives only shows", {i["media_type"] for i in out["items"]} == {"tv"}, titles(out))

    print("\n[2] The wish moves the order")
    out = wm.recommend(query="make me laugh", media_type="movie", page_size=10)
    check("'make me laugh' puts funny films on top", all(t.startswith("Funny Film") for t in titles(out)[:10]),
          titles(out))
    check("each pick carries reason words", all(i["taste_reasons"] for i in out["items"]), out["items"][:1])
    out = wm.recommend(query="something tense", media_type="movie", page_size=10)
    check("content warnings are never shown as reasons",
          not any("animal_harm" in i["taste_reasons"] for i in out["items"]), [i["taste_reasons"] for i in out["items"]])
    check("'tense' puts tense films on top", all("Tense" in t or "Thriller" in t or "Horror" in t
                                                  for t in titles(out)[:10]), titles(out))

    print("\n[3] Typed genres and places filter; the foreign rule holds")
    out = wm.recommend(query="comedy", media_type=None, page_size=50)
    check("'comedy' gives only comedies", out["items"] and all("Comedy" in (i["genres"] or "") for i in out["items"]),
          titles(out))
    out = wm.recommend(query="anything tense but no horror", media_type="movie", page_size=50)
    check("'no horror' leaves horror out", "Horror Film" not in titles(out), titles(out))
    out = wm.recommend(query="korean thriller", media_type=None, page_size=50)
    check("'korean thriller' gives only Korean thrillers",
          set(titles(out)) == {"Ordinary Korean Thriller", "Top Korean Thriller"}, titles(out))
    out = wm.recommend(query="tense", media_type="movie", page_size=50)
    t = titles(out)
    check("an ordinary Korean film is left out when no place is named", "Ordinary Korean Thriller" not in t, t)
    check("a top-tier Korean film still passes", "Top Korean Thriller" in t, t)

    print("\n[4] Little-known titles only for hidden gems")
    out = wm.recommend(query="funny", media_type="movie", page_size=50)
    check("a title Haiku didn't know is left out", "Obscure Funny Film" not in titles(out), titles(out))
    out = wm.recommend(query="funny hidden gem", media_type="movie", page_size=50)
    check("'hidden gem' lets it in", "Obscure Funny Film" in titles(out), titles(out))

    print("\n[5] Seen titles and tastes")
    for i in range(6):
        rate(db, 1, ids[f"Funny Film {i}"], 9)
        rate(db, 2, ids[f"Tense Film {i}"], 9)
    rate(db, 1, ids["Tense Film 6"], 3)
    rate(db, 2, ids["Funny Film 6"], 3)
    out = wm.recommend(media_type="movie", profile="me", page_size=50)
    t = titles(out)
    check("alone, what you have seen is left out", "Funny Film 0" not in t and "Tense Film 6" not in t, t)
    check("alone, what only the other person saw stays", "Tense Film 0" in t, t)
    check("with no wish, your taste leads ('me' gets funny films first)", t[0].startswith("Funny Film"), t[:5])
    out = wm.recommend(media_type="movie", profile="partner", page_size=50)
    check("the partner gets tense films first", titles(out)[0].startswith("Tense Film"), titles(out)[:5])
    out = wm.recommend(media_type="movie", profile="together", page_size=50)
    t = titles(out)
    check("together, a film only one of you saw stays", "Funny Film 0" in t, t)
    me_alone = wm.recommend(media_type="movie", profile="me", page_size=50)["items"]
    tog = out["items"]
    worst_alone = {i["title"]: i["taste_fit"] for i in me_alone}
    worst_tog = {i["title"]: i["taste_fit"] for i in tog}
    check("together pushes down what one of you dislikes",
          worst_tog["Funny Film 8"] < worst_alone["Funny Film 8"], (worst_tog.get("Funny Film 8"),
                                                                     worst_alone.get("Funny Film 8")))

    print("\n[6] Shapes other screens read")
    pool = wm.recommend(media_type="movie", profile="together", return_pool=True, only_ids=[ids["Funny Film 9"],
                                                                                            ids["Tense Film 9"]])
    check("return_pool gives a pool of only those ids",
          sorted(i["title"] for i in pool["pool"]) == ["Funny Film 9", "Tense Film 9"], pool)
    out = wm.recommend(page=2, page_size=5, media_type="movie")
    check("paging works", out["page"] == 2 and len(out["items"]) == 5 and out["has_more"], out)
    check("the wish is reported", wm.read_wish("not scary, make us laugh")["avoid_tags"], wm.read_wish("not scary"))
    status = wm.status()
    check("status counts tagged titles by type", status["tagged_titles"] == 42 and "tv" in status["by_type"], status)

    print("\n[7] The engine switch")
    legacy = RecommenderService(db, shortlist_size=50, final_pool=60)

    class FakeTiger:
        engine_mode = "fallback"

        def is_ready(self):
            return False

    def switch(name, wish=wm):
        return EngineSwitch(name, legacy, FakeTiger(), wish, route_recommend, "fallback")

    out = switch("legacy").route(query="comedy", media_type="movie", page_size=5)
    check("legacy answers when the switch says legacy", out.get("engine") == "legacy", out.get("engine"))
    out = switch("wishmatch").route(query="comedy", media_type="movie", page_size=5)
    check("wishmatch answers when the switch says wishmatch", out.get("engine") == "wishmatch", out.get("engine"))

    class Broken:
        def recommend(self, **kw):
            raise RuntimeError("no tags")

    out = switch("wishmatch", Broken()).route(query="comedy", media_type="movie", page_size=5)
    check("a Wishmatch failure falls back to legacy", out.get("engine") == "legacy" and out.get("wishmatch_error"), out)
    out = switch("wishmatch").recommend(media_type="movie", profile="together", return_pool=True, page_size=5)
    check("the Northern Lights entry point answers with Wishmatch", out.get("engine") == "wishmatch" and out["pool"], out)
    check("other attributes pass through to legacy", switch("wishmatch").final_pool == 60)
    check("engine_name reads config and refuses unknown names",
          engine_name({"engine": "WishMatch"}) == "wishmatch" and engine_name({"engine": "x"}) == "legacy"
          and engine_name({}) == "legacy")

    print("\n[8] The wishmatch: rules only use vocabulary words")
    rules = load_wishmatch_rules()
    lex = rules.get("lexicon") or {}
    tag_words = set(vibe_vocab.FEELS + vibe_vocab.GOOD_FOR + vibe_vocab.SETTING + vibe_vocab.SEASON_WEATHER
                    + vibe_vocab.CONTENT_FLAGS)
    bad_traits = [(w, t) for w, ts in (lex.get("traits") or {}).items() for t in ts if t not in vibe_vocab.DIALS]
    bad_tags = [(w, t) for w, ts in (lex.get("tags") or {}).items() for t in ts if t not in tag_words]
    check("every trait in the lexicon is a vibe_vocab dial", not bad_traits, bad_traits)
    check("every tag in the lexicon is a vibe_vocab tag", not bad_tags, bad_tags)
    check("the settings name the other person's weight, pool size, split and question limit",
          rules["people"]["other_weight"] == 0.2 and rules["candidates"]["total"] == 2000
          and set(rules["candidates"]["kinds"]) == {"movie", "tv"} and rules["questions"]["max"] == 2
          and "tagging_model" in rules, rules.get("people"))

    if failures:
        print(f"\n{len(failures)} check(s) failed")
        sys.exit(1)
    print("\nall checks passed")


if __name__ == "__main__":
    main()
