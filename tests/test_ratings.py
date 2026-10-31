"""
Offline co-view rating tests (tests/test_ratings.py)

Pure-SQLite: no network, no TMDB, no Jellyfin. Uses a throwaway database in
tempdir so the real catalogues are never touched.

Run standalone:

    uv run python tests/test_ratings.py
"""

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import DatabaseManager  # noqa: E402

failures = []


def check(label, condition, detail=""):
    if condition:
        print(f"  ✅ {label}")
    else:
        print(f"  ❌ {label} — got: {detail}")
        failures.append(label)


def build_db(path):
    """Creates a small catalogue: 5 films with distinct genres."""
    db = DatabaseManager(path)
    with db.get_connection() as conn:
        rows = [
            ("Inception", 2010, 8.4, 41000, "Action, Science Fiction, Adventure"),
            ("Interstellar", 2014, 8.7, 38000, "Science Fiction, Drama, Adventure"),
            ("Jaws", 1975, 8.1, 12000, "Horror, Thriller, Adventure"),
            ("Se7en", 1995, 8.4, 23887, "Crime, Mystery, Thriller"),
            ("Titanic", 1997, 7.9, 21000, "Drama, Romance"),
        ]
        for title, year, rating, votes, genres in rows:
            conn.execute(
                "INSERT INTO media_items (title, release_year, media_type, rating, vote_count, genres) "
                "VALUES (?, ?, 'movie', ?, ?, ?);",
                (title, year, rating, votes, genres),
            )
        # Inception "in the library" -> must lead the queue.
        conn.execute(
            "INSERT INTO media_sources (media_item_id, file_path, parsed_title) "
            "VALUES (1, '/media/inception.mkv', 'Inception');"
        )
        # A TV show, to prove media_type filtering works.
        conn.execute(
            "INSERT INTO media_items (title, release_year, media_type, rating, vote_count, genres) "
            "VALUES ('Severance', 2022, 'tv', 8.7, 12000, 'Drama, Science Fiction, Mystery');"
        )
    return db


def test_migration(db):
    print("\n[1] Migration v3 — profiles, profile_id, status")
    profiles = db.get_profiles()
    check("two profiles seeded", len(profiles) == 2, profiles)
    check("profile ids are 1 and 2", [p["id"] for p in profiles] == [1, 2])

    with db.get_connection() as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(watch_history);")}
    check("watch_history.profile_id exists", "profile_id" in cols)
    check("watch_history.status exists", "status" in cols)

    # Idempotency: a second init must not error or duplicate rows.
    DatabaseManager(db.db_path)
    check("re-init is idempotent", len(db.get_profiles()) == 2)


def test_not_seen(db):
    print("\n[2] 'Haven't seen it' is a real state, not a low rating")
    db.rate_item(1, profile_id=1, user_rating=9.5)
    summary = db.rate_item(1, profile_id=2, status="not_seen")

    check("status is not_seen", summary["verdicts"][1]["status"] == "not_seen")
    check("no rating stored with not_seen", summary["verdicts"][1]["user_rating"] is None)
    check("tier is B (one loved, one hasn't)", summary["tier"] == "B", summary["tier"])
    check("combined score only counts the rater who saw it",
          summary["combined_score"] is not None)

    # A stray rating alongside not_seen must be discarded.
    summary = db.rate_item(1, profile_id=2, user_rating=3.0, status="not_seen")
    check("rating dropped when status=not_seen",
          summary["verdicts"][1]["user_rating"] is None)


def test_tiers(db):
    print("\n[3] Co-view tiers")
    db.rate_item(2, profile_id=1, user_rating=9.8)
    db.rate_item(2, profile_id=2, user_rating=8.4)
    s = db.get_rating_summary(2)
    check("tier A when both rate >= 7", s["tier"] == "A", s["tier"])
    check("combined score computed", s["combined_score"] is not None)
    check("disagreement reported", abs(s["disagreement"] - 1.4) < 0.01, s["disagreement"])

    db.rate_item(3, profile_id=1, user_rating=9.0)
    db.rate_item(3, profile_id=2, user_rating=2.0)
    s = db.get_rating_summary(3)
    check("tier C when seen but disliked", s["tier"] == "C", s["tier"])
    check("large disagreement surfaced", s["disagreement"] >= 6.0, s["disagreement"])

    s = db.get_rating_summary(5)
    check("tier C when nobody has seen it", s["tier"] == "C", s["tier"])
    check("no combined score without raters", s["combined_score"] is None)


def test_upsert(db):
    print("\n[4] One rating per profile per item (upsert)")
    before = db.rate_item(2, profile_id=1, user_rating=9.8)
    after = db.rate_item(2, profile_id=1, user_rating=6.0)
    check("re-rating updates in place", after["verdicts"][0]["user_rating"] == 6.0)
    check("tier drops after a bad re-rate", after["tier"] == "C", after["tier"])

    with db.get_connection() as conn:
        n = conn.execute(
            "SELECT COUNT(*) FROM watch_history WHERE media_item_id = 2 AND profile_id = 1;"
        ).fetchone()[0]
    check("no duplicate rows created", n == 1, f"rows={n}")
    db.rate_item(2, profile_id=1, user_rating=9.8)


def test_shrinkage(db):
    print("\n[5] Shrinkage — prolific raters dominate")
    # Profile 1: many ratings, consistently high. Profile 2: one rating, low.
    # NB: the filler INSERTs commit inside their own `with` block — calling
    # rate_item() while a connection is open deadlocks on the SQLite write lock.
    with db.get_connection() as conn:
        for i in range(20):
            conn.execute(
                "INSERT INTO media_items (title, release_year, media_type, rating, vote_count, genres) "
                "VALUES (?, 2020, 'movie', 7.5, 5000, 'Drama');",
                (f"Filler {i}",),
            )
        ids = [r[0] for r in conn.execute(
            "SELECT id FROM media_items WHERE title LIKE 'Filler %';")]
        for i in ids:
            conn.execute(
                "INSERT INTO watch_history (media_item_id, profile_id, user_rating, status) "
                "VALUES (?, 1, 9.0, 'seen');", (i,))

    # Item 4 (Se7en): profile1 (n=21, mean 9.0) likes it, profile2 (n=1) dislikes it.
    db.rate_item(4, profile_id=1, user_rating=9.0)
    db.rate_item(4, profile_id=2, user_rating=3.0)
    s = db.get_rating_summary(4)
    check("heavy rater outweighs a single low rating",
          s["combined_score"] > 7.0, f"combined={s['combined_score']}")
    check("disagreement case is tier C, not B", s["tier"] == "C", s["tier"])


def test_queue(build):
    print("\n[6] Queue prioritises per-viewer, balanced, rotating")
    # Fresh DB: earlier tests rate items and add filler rows, which would
    # exclude candidates (exclude_watched) and crowd out the library item.
    db = build()
    # Profile 2 sees in-library first; profile 1 sees other-rated first.
    q2 = db.get_rating_queue(profile_id=2, limit=10, seed=1)
    check("in-library item leads the partner queue",
          q2[0]["title"] == "Inception", [i["title"] for i in q2[:3]])
    check("library flag set on it", q2[0]["in_library"] == 1, q2[0]["in_library"])

    # Profile 1 rated nothing here, so its queue leads with the co-view gap.
    db.rate_item(3, profile_id=2, user_rating=8.0)   # Jaws rated by partner
    q1 = db.get_rating_queue(profile_id=1, limit=10, seed=1)
    check("partner-rated film leads MY queue", q1[0]["title"] == "Jaws",
          [i["title"] for i in q1[:3]])
    check("it is flagged as rated by the other profile",
          q1[0]["other_profile_count"] == 1, q1[0]["other_profile_count"])

    check("TV excluded from a movie queue",
          all(i["media_type"] == "movie" for i in q1))
    tv_queue = db.get_rating_queue(profile_id=2, media_type="tv", limit=10)
    check("TV queue returns only TV", [i["media_type"] for i in tv_queue] == ["tv"])

    # Already-rated items drop out. Jaws was rated by partner above and is
    # still unrated by me, so it must remain in MY queue while being gone
    # from the partner's (who already rated it).
    check("rated item leaves that profile's queue",
          "Jaws" not in [i["title"] for i in db.get_rating_queue(profile_id=2, limit=10, seed=1)])
    check("item rated only by the other profile stays in MY queue",
          "Jaws" in [i["title"] for i in db.get_rating_queue(profile_id=1, limit=10, seed=1)])
    db.rate_item(3, profile_id=1, user_rating=9.0)   # now rated by both
    check("rated by both -> gone from both queues",
          "Jaws" not in [i["title"] for i in db.get_rating_queue(profile_id=1, limit=10, seed=1)]
          and "Jaws" not in [i["title"] for i in db.get_rating_queue(profile_id=2, limit=10, seed=1)])

    # Genre balance fixture: needs a pool with real genre variety before the
    # rotation/balance assertions mean anything.
    with db.get_connection() as conn:
        n = conn.execute("SELECT COUNT(*) FROM media_items").fetchone()[0]
        for i in range(60):
            for genre in ("Comedy, Drama", "Animation, Action", "Horror, Thriller",
                          "Romance, Drama", "Science Fiction, Adventure"):
                conn.execute(
                    "INSERT INTO media_items (title, release_year, media_type, rating, vote_count, genres) "
                    "VALUES (?, 2021, 'movie', 7.9, 9000, ?);",
                    (f"Spread {n + i} {genre[:3]}", genre),
                )

    # Determinism + rotation (on a pool big enough to rotate).
    a = [i["id"] for i in db.get_rating_queue(profile_id=1, limit=8, seed=7)]
    b = [i["id"] for i in db.get_rating_queue(profile_id=1, limit=8, seed=7)]
    c = [i["id"] for i in db.get_rating_queue(profile_id=1, limit=8, seed=8)]
    check("same seed is stable", a == b, (a, b))
    check("different seed rotates the queue", a != c, (a, c))

    # Genre filter.
    comedy = db.get_rating_queue(profile_id=1, genres=["Comedy"], limit=10, seed=1)
    check("genre filter returns only that genre",
          comedy and all("Comedy" in (i["genres"] or "") for i in comedy),
          [i["genres"] for i in comedy])
    check("genre filter is non-empty", len(comedy) > 0)
    multi = db.get_rating_queue(profile_id=1, genres=["Horror", "Animation"], limit=10, seed=1)
    check("multiple genres OR together",
          multi and all(("Horror" in (i["genres"] or "")) or ("Animation" in (i["genres"] or ""))
                        for i in multi), [i["genres"] for i in multi])
    check("unknown genre returns empty",
          db.get_rating_queue(profile_id=1, genres=["Polka"], limit=10, seed=1) == [])

    spread = db.get_rating_queue(profile_id=1, limit=25, seed=3)
    primaries = [(i["genres"] or "").split(",")[0].strip() for i in spread]
    check("queue is not dominated by one primary genre",
          len(set(primaries)) >= 4, sorted(set(primaries)))
    check("genre balance keeps films from several registers",
          any("Comedy" in (i["genres"] or "") for i in spread)
          and any("Animation" in (i["genres"] or "") for i in spread),
          primaries)


def test_co_view_history(db):
    print("\n[7] Virtual-user sequence (both rated >= 7)")
    items = db.get_co_view_history(min_liked=7.0)
    ids = [i["id"] for i in items]
    check("only both-rated items included", 2 in ids, ids)
    check("single-rater item excluded", 1 not in ids, ids)
    check("disliked item excluded", 3 not in ids, ids)
    check("ordered by combined score", items == sorted(
        items, key=lambda x: -x["avg_rating"]))


def test_validation(db):
    print("\n[8] Input validation")
    for label, kwargs in [
        ("bad status", dict(media_item_id=1, profile_id=1, status="maybe")),
        ("unknown item", dict(media_item_id=999999, profile_id=1, user_rating=8)),
        ("unknown profile", dict(media_item_id=1, profile_id=99, user_rating=8)),
    ]:
        try:
            db.rate_item(**kwargs)
            check(f"rejects {label}", False, "no error raised")
        except ValueError:
            check(f"rejects {label}", True)

    s = db.rate_item(5, profile_id=1, user_rating=99.0)
    check("rating clamped to 10", s["verdicts"][0]["user_rating"] == 10.0)

    check("rename works", db.update_profile_name(2, "Partner Two"))
    check("duplicate name rejected", not db.update_profile_name(2, "Person 1"))
    check("empty name rejected", not db.update_profile_name(2, "   "))
    check("unknown id rejected", not db.update_profile_name(42, "Nobody"))


def test_genre_sql(build):
    print("\n[9] Genre filter executes in SQL, not Python")
    db = build()
    # exclude_watched=False: earlier tests rate these films, and the genre
    # assertions are about matching, not about watch exclusion.
    horror = db.get_top_candidates(
        media_type="movie", genres=["Horror"], exclude_watched=False,
        min_rating=6.0, limit=10,
    )
    check("genre filter returns matches", len(horror) == 1, [i["title"] for i in horror])
    check("correct item matched", horror[0]["title"] == "Jaws", [i["title"] for i in horror])

    # Multi-word genres must survive the comma-token match (space handling).
    scifi = db.get_top_candidates(
        media_type="movie", genres=["Science Fiction"], exclude_watched=False,
        min_rating=6.0, limit=10,
    )
    check("multi-word genre matches (space handling)",
          {i["title"] for i in scifi} == {"Inception", "Interstellar"},
          [i["title"] for i in scifi])

    # OR semantics across multiple requested genres.
    multi = db.get_top_candidates(
        media_type="movie", genres=["Horror", "Romance"], exclude_watched=False,
        min_rating=6.0, limit=10,
    )
    check("multiple genres OR together",
          {i["title"] for i in multi} == {"Jaws", "Titanic"},
          [i["title"] for i in multi])

    check("unknown genre returns empty", db.get_top_candidates(
        media_type="movie", genres=["Polka"], exclude_watched=False,
        min_rating=6.0, limit=10) == [])

    # No genre requested -> pure quality cut, still limited.
    everything = db.get_top_candidates(
        media_type="movie", genres=None, exclude_watched=False,
        min_rating=6.0, limit=3,
    )
    check("no-genre query respects limit", len(everything) == 3, len(everything))
    check("ordered by Bayesian quality",
          [i["title"] for i in everything][0] == "Interstellar",
          [i["title"] for i in everything])

    # Case-insensitive, and a partial genre must NOT match.
    check("case-insensitive match", len(db.get_top_candidates(
        media_type="movie", genres=["horror"], exclude_watched=False,
        min_rating=6.0, limit=10)) == 1)
    check("partial genre does not match", db.get_top_candidates(
        media_type="movie", genres=["Horr"], exclude_watched=False,
        min_rating=6.0, limit=10) == [])


def test_profile_scoring(build):
    print("\n[10] Profile-aware scoring: tiers, blending, viewer-relative")
    from services.recommender_service import PROFILE_MIX, TIER_BOOSTS, RecommenderService
    db = build()

    # Profile 1 likes thrillers; profile 2 likes animation. Both love Se7en.
    db.rate_item(1, profile_id=1, user_rating=9.0)   # Inception
    db.rate_item(2, profile_id=2, user_rating=8.5)   # Interstellar (B for me)
    db.rate_item(3, profile_id=1, user_rating=9.0)   # Jaws
    db.rate_item(3, profile_id=2, user_rating=8.0)   # Jaws -> tier A
    db.rate_item(4, profile_id=1, user_rating=9.5)   # Se7en (I liked, he hasn't)

    # Viewer-relative tiers: the whole point is that "B" means
    # "the OTHER person loved it and YOU haven't".
    v_me = db.get_item_co_view([2, 3, 4], viewer_profile_id=1)
    check("film the other liked and I haven't -> tier B (me)", v_me[2]["tier"] == "B",
          v_me[2]["tier"])
    check("shared favourite -> tier A (me)", v_me[3]["tier"] == "A", v_me[3]["tier"])
    check("film only I liked -> tier C for me", v_me[4]["tier"] == "C", v_me[4]["tier"])

    v_him = db.get_item_co_view([2, 3, 4], viewer_profile_id=2)
    check("my film he hasn't seen -> tier B (him)", v_him[4]["tier"] == "B", v_him[4]["tier"])
    check("shared favourite -> tier A (him)", v_him[3]["tier"] == "A", v_him[3]["tier"])
    check("film he already rated -> tier C (him)", v_him[2]["tier"] == "C", v_him[2]["tier"])

    # Symmetric view (rating UI) still works with no viewer.
    sym = db.get_item_co_view([2, 3, 4])
    check("symmetric tier A for shared favourite", sym[3]["tier"] == "A", sym[3]["tier"])

    check("tier boosts are ordered A > B > C",
          TIER_BOOSTS["A"] > TIER_BOOSTS["B"] > TIER_BOOSTS["C"] == 0.0, TIER_BOOSTS)
    check("together weights are asymmetric toward the viewer",
          PROFILE_MIX["together"][0] > PROFILE_MIX["together"][1], PROFILE_MIX)
    # "own" means the viewer, never profile 1 — so me/partner share weights
    # and the id swap re-points "own". Encoding a lower own-weight for partner
    # once ranked the viewer's own taste first in "his picks".
    check("single-profile views weight the viewer above the other",
          PROFILE_MIX["me"][0] > PROFILE_MIX["me"][1]
          and PROFILE_MIX["partner"][0] > PROFILE_MIX["partner"][1], PROFILE_MIX)
    check("me and partner use the same own-weight (ids are swapped instead)",
          PROFILE_MIX["me"] == PROFILE_MIX["partner"], PROFILE_MIX)

    # Ranking: profile-aware path engages and me != partner.
    # The fixtures above are all watched by *someone*, and every profile mode
    # now excludes anything anyone has seen — that is the point, do not
    # recommend a film back to someone who watched it. So each profile gets a
    # distinct taste to rank the UNWATCHED remainder by.
    # The 5-fixture catalogue is fully watched by someone after the ratings
    # above, and every profile mode now excludes anything anyone has seen. So
    # grow a distinct catalogue with clear genre identities for each profile.
    with db.get_connection() as conn:
        conn.execute("DELETE FROM media_items WHERE media_type = 'movie';")
        extra = [
            ("Anim One", "Animation, Family"), ("Anim Two", "Animation, Adventure"),
            ("Scary One", "Horror"), ("Scary Two", "Horror, Thriller"),
            ("Rom One", "Romance, Drama"), ("Rom Two", "Romance, Comedy"),
            ("Fun One", "Comedy"), ("Fun Two", "Comedy, Family"),
        ]
        for title, g in extra:
            conn.execute(
                "INSERT INTO media_items (title, release_year, media_type, rating, "
                "vote_count, genres) VALUES (?, 2020, 'movie', 7.8, 5000, ?);", (title, g)
            )
        ids = [r[0] for r in conn.execute(
            "SELECT id FROM media_items WHERE media_type='movie' ORDER BY id;").fetchall()]
    db.rate_item(ids[0], profile_id=1, user_rating=9.0)   # me likes animation
    db.rate_item(ids[3], profile_id=2, user_rating=9.0)   # he likes horror

    r = RecommenderService(db, shortlist_size=50, final_pool=20)
    together = r.recommend(mood_id=8, media_type="movie", profile="together", page=1, page_size=10)
    check("profile-aware scoring used (tier field present)",
          "co_view_tier" in together["items"][0], list(together["items"][0].keys())[:6])

    me = r.recommend(mood_id=8, media_type="movie", profile="me", page=1, page_size=10)
    him = r.recommend(mood_id=8, media_type="movie", profile="partner", page=1, page_size=10)
    check("'me' and 'partner' are NOT the same list",
          [i["id"] for i in me["items"]] != [i["id"] for i in him["items"]],
          ([i["id"] for i in me["items"][:4]], [i["id"] for i in him["items"][:4]]))
    check("own/other taste bonuses actually differ",
          any(i["taste_bonus_own"] != i["taste_bonus_other"] for i in me["items"]),
          [(i["taste_bonus_own"], i["taste_bonus_other"]) for i in me["items"][:3]])
    # Watched exclusion is now universal: nothing any profile has seen comes back.
    # ids[0] and ids[3] were just rated by someone, so both views must omit
    # them — the exclusion is universal, not "seen by both".
    watched = {ids[0], ids[3]}
    check("films already watched by anyone are excluded from both views",
          not (watched & {i["id"] for i in me["items"]})
          and not (watched & {i["id"] for i in him["items"]}),
          (sorted(i["id"] for i in me["items"]), sorted(i["id"] for i in him["items"])))
    check("me ranks animation first", me["items"][0]["genres"].startswith("Animation"),
          (me["items"][0]["title"], me["items"][0]["genres"]))
    check("he ranks horror first", him["items"][0]["genres"].startswith("Horror"),
          (him["items"][0]["title"], him["items"][0]["genres"]))

    # The tier boost must actually change the order.
    a_item = [i for i in together["items"] if i["co_view_tier"] == "A"]
    c_item = [i for i in together["items"] if i["co_view_tier"] == "C"]
    check("tier A item outranks a lower-scoring tier C item",
          not (a_item and c_item
               and a_item[0]["base_weighted_rating"] < c_item[0]["base_weighted_rating"]
               and a_item[0]["recommendation_score"] < c_item[0]["recommendation_score"]),
          (a_item[0]["recommendation_score"] if a_item else None,
           c_item[0]["recommendation_score"] if c_item else None))


def test_balanced_queue(build):
    print("\n[11] Balanced pass walks the genre wheel")
    db = build()
    with db.get_connection() as conn:
        n = conn.execute("SELECT COUNT(*) FROM media_items").fetchone()[0]
        combos = ["Comedy, Drama", "Animation, Action", "Horror, Thriller", "Romance, Drama",
                  "Science Fiction, Adventure", "Family, Music", "Western, Drama"]
        for i in range(30):
            for g in combos:
                conn.execute(
                    "INSERT INTO media_items (title, release_year, media_type, rating, vote_count, genres) "
                    "VALUES (?, 2020, 'movie', 7.9, 9000, ?);",
                    (f"Wheel {n + i} {g[:4]}", g),
                )
    q = db.get_rating_queue(profile_id=1, limit=20, balanced=True, seed=1)
    check("balanced queue returns items", len(q) > 0, len(q))
    tags = set()
    for i in q:
        for g in (i["genres"] or "").split(","):
            tags.add(g.strip())
    check("balanced covers many genres", len(tags) >= 5, sorted(tags))
    check("balanced surfaces Comedy", any("Comedy" in (i["genres"] or "") for i in q))
    check("balanced surfaces Animation", any("Animation" in (i["genres"] or "") for i in q))
    check("no duplicate ids", len({i["id"] for i in q}) == len(q))
    # Same seed is stable; different seed still valid.
    q2 = db.get_rating_queue(profile_id=1, limit=20, balanced=True, seed=1)
    check("balanced is deterministic per seed",
          [i["id"] for i in q] == [i["id"] for i in q2])
    check("balanced respects limit", len(q) <= 20, len(q))


def test_query_intent(build):
    print("\n[12] Free-text query intent: exclusions, negation, mood genres")
    from services.recommender_service import (
        MOOD_MAPPINGS, parse_query_intent, resolve_effective_genres,
    )
    db = build()

    q = ("We would like to see a high quality movie which is relaxing to watch "
         "but not too complicated, it should be a bit funny, but it should have "
         "good concepts.")

    intent = parse_query_intent(q)
    check("'relaxing' is recognised as intent, not a content word",
          "relaxing" in intent["matched_terms"], intent["matched_terms"])
    check("'funny' pulls in Comedy", "Comedy" in intent["boost_genres"],
          intent["boost_genres"])
    check("'relaxing' excludes violent genres",
          {"Crime", "Horror", "Thriller"} <= set(intent["exclude_genres"]),
          intent["exclude_genres"])
    check("generic adjectives are dropped as content words",
          not ({"high", "quality", "movie", "good", "want"} & set(intent["content_words"])),
          intent["content_words"])
    check("intent weight is bounded", 0 < intent["weight"] <= 1.0, intent["weight"])

    # "not too complicated" must not be read as a request for complexity.
    neg = parse_query_intent("something scary not relaxing")
    check("a negated tone is dropped, the rest of the request stays",
          "Horror" in neg["boost_genres"] and "Horror" not in neg["exclude_genres"], neg)

    # Relaxing mood should not map to Documentary (nature docs / preschool TV).
    check("relaxing mood includes Comedy",
          "Comedy" in MOOD_MAPPINGS[7]["genres"], MOOD_MAPPINGS[7]["genres"])
    check("relaxing mood dropped Documentary",
          "Documentary" not in MOOD_MAPPINGS[7]["genres"], MOOD_MAPPINGS[7]["genres"])

    # Chips + intent combine, with exclusions surviving the merge.
    spec = resolve_effective_genres(
        MOOD_MAPPINGS[7]["genres"], ["Action", "Mystery"], q)
    check("chips are kept in the include set",
          {"Action", "Mystery"} <= set(spec["include"]), spec["include"])
    check("query exclusions survive the chip merge",
          "Thriller" in spec["exclude"], spec["exclude"])

    # A mood must never veto its own genres (mood 4 = Horror must return horror).
    scary = resolve_effective_genres(MOOD_MAPPINGS[4]["genres"], [], "I want something scary")
    check("a scary query does not exclude Horror", "Horror" not in scary["exclude"],
          scary["exclude"])

    # A genre typed by name with a negation is a hard exclusion, and the word
    # must not stay behind as a content word that boosts the very genre.
    no_anim = parse_query_intent("something funny tonight, no animation")
    check("'no animation' excludes Animation", "Animation" in no_anim["exclude_genres"],
          no_anim["exclude_genres"])
    check("'no animation' does not boost animation as a content word",
          "animation" not in no_anim["content_words"], no_anim["content_words"])
    listed = parse_query_intent("nothing animated or musicals please")
    check("a negated list of genres excludes each one",
          {"Animation", "Music"} <= set(listed["exclude_genres"]), listed["exclude_genres"])
    check("'less animation' is not a hard exclusion",
          not parse_query_intent("less animation")["exclude_genres"])
    check("an un-negated genre name is not excluded",
          not parse_query_intent("a funny animation")["exclude_genres"])
    check("a negated word is never a content word",
          "zombies" not in parse_query_intent("no zombies please")["content_words"])
    relax = resolve_effective_genres(MOOD_MAPPINGS[7]["genres"], None, "relaxing, no animation")
    check("a named exclusion beats the mood's own genres",
          "Animation" in relax["exclude"] and "Animation" not in relax["include"], relax)


def test_genre_exclusion_sql(build):
    print("\n[13] SQL genre exclusion returns rows (regression: NOT LIKE trap)")
    db = build()
    with db.get_connection() as conn:
        for i in range(40):
            conn.execute(
                "INSERT INTO media_items (title, release_year, media_type, rating, "
                "vote_count, genres) VALUES (?, 2020, 'movie', 7.5, 9000, ?);",
                (f"Clean {i}", "Animation, Comedy, Family"),
            )
        for i in range(40):
            conn.execute(
                "INSERT INTO media_items (title, release_year, media_type, rating, "
                "vote_count, genres) VALUES (?, 2020, 'movie', 7.5, 9000, ?);",
                (f"Bad {i}", "Animation, Thriller, Crime"),
            )

    keep = db.get_top_candidates(media_type="movie", genres=["Animation"],
                                 exclude_watched=False, min_rating=6.0, limit=500)
    check("include-only returns both groups", len(keep) == 80, len(keep))

    clean = db.get_top_candidates(media_type="movie", genres=["Animation"],
                                  exclude_genres=["Thriller", "Crime"],
                                  exclude_watched=False, min_rating=6.0, limit=500)
    # Regression guard: `NOT (a NOT LIKE .. OR b NOT LIKE ..)` returned 0 rows
    # in SQLite because a falsy NOT LIKE is not inverted by the outer NOT.
    check("exclusion keeps the clean rows (NOT LIKE trap regression)",
          len(clean) == 40, len(clean))
    check("exclusion drops the contradicting rows",
          all("Clean" in c["title"] for c in clean),
          [c["title"] for c in clean[:3]])

    # A genre nobody has must not empty the result set.
    none_match = db.get_top_candidates(media_type="movie", genres=["Animation"],
                                       exclude_genres=["Musical"],
                                       exclude_watched=False, min_rating=6.0, limit=500)
    check("excluding an absent genre changes nothing", len(none_match) == 80,
          len(none_match))


def test_pagination_shape(build):
    print("\n[14] Pagination: pages are disjoint and cover the pool")
    db = build()
    # The shared fixture catalogue is small; a 50-item pool needs 50+ rows to
    # assert page 1 is full. Grow it rather than weakening the assertions.
    with db.get_connection() as conn:
        n = conn.execute("SELECT COUNT(*) FROM media_items").fetchone()[0]
        for i in range(70):
            conn.execute(
                "INSERT INTO media_items (title, release_year, media_type, rating, "
                "vote_count, genres) VALUES (?, 2020, 'movie', 7.5, 9000, ?);",
                (f"Pool {n + i}", "Drama, Comedy"),
            )
    from services.recommender_service import RecommenderService
    r = RecommenderService(db, shortlist_size=250, final_pool=50)

    p1 = r.recommend(mood_id=8, media_type="movie", profile="together",
                     page=1, page_size=20)
    check("page 1 is full", len(p1["items"]) == 20, len(p1["items"]))
    check("page 1 advertises more", p1["has_more"] is True, p1.get("has_more"))
    check("total reflects the pool, not the page", p1["total_results"] == 50,
          p1["total_results"])

    p2 = r.recommend(mood_id=8, media_type="movie", profile="together",
                     page=2, page_size=20)
    p3 = r.recommend(mood_id=8, media_type="movie", profile="together",
                     page=3, page_size=20)
    ids = [i["id"] for pg in (p1, p2, p3) for i in pg["items"]]
    check("pages do not repeat items", len(ids) == len(set(ids)),
          f"{len(ids)} ids, {len(set(ids))} unique")
    check("pagination reaches the whole pool", len(ids) == 50, len(ids))
    check("last page reports no more", p3["has_more"] is False, p3.get("has_more"))
    check("page beyond the pool is empty",
          r.recommend(mood_id=8, media_type="movie", profile="together",
                      page=99, page_size=20)["items"] == [])


def test_rating_genre_endpoint(build):
    print("\n[15] Rating-queue genre chips reflect the catalogue")
    import asyncio
    import server  # noqa: F401  (builds the app)
    from web import state
    from web.routes import ratings

    db = build()
    with db.get_connection() as conn:
        for i in range(30):
            conn.execute(
                "INSERT INTO media_items (title, release_year, media_type, rating, "
                "vote_count, genres) VALUES (?, 2020, 'movie', 7.5, 9000, ?);",
                (f"Big {i}", "Drama, Comedy"),
            )
        # A genre too small to fill a page must not be offered as a chip.
        conn.execute(
            "INSERT INTO media_items (title, release_year, media_type, rating, "
            "vote_count, genres) VALUES ('Rare', 2020, 'movie', 7.5, 9000, 'Curtain');"
        )

    original = state.db
    state.db = db
    try:
        payload = asyncio.run(ratings.list_rating_genres())
    finally:
        state.db = original

    names = {g["name"] for g in payload["genres"]}
    check("a genre with enough films is offered", "Comedy" in names, sorted(names))
    check("a genre too small to fill a page is omitted", "Curtain" not in names,
          sorted(names))
    check("counts are reported per genre",
          all(isinstance(g.get("count"), int) and g["count"] > 0 for g in payload["genres"]),
          payload["genres"][:2])
    # Every offered chip must be able to fill a page — the bug was a chip that
    # produced a 1–2 card queue and looked broken.
    for g in payload["genres"]:
        q = db.get_rating_queue(profile_id=1, limit=25, genres=[g["name"]],
                                balanced=True, seed=3)
        check(f"chip '{g['name']}' fills a 25-card queue",
              len(q) >= 20, len(q))


def test_download_survives_refresh(build):
    print("\n[16] Downloads are server-owned, so a browser refresh can't stop them")
    import inspect
    from services.torrent_service import downloads_store
    import server  # noqa: F401  (builds the app)
    from web import state
    from web.routes import torrents

    src = inspect.getsource(torrents.download_torrent)
    check("download is dispatched with asyncio.create_task",
          "asyncio.create_task" in src)
    check("no binding to an HTTP client session/websocket",
          not any(k in src for k in ("WebSocket", "request.state", "BackgroundTask")))
    check("progress state lives in a module-level dict",
          isinstance(downloads_store, dict))
    check("the store is importable from outside the request handler",
          downloads_store is state.downloads_store)



def test_resume_and_eta(build):
    print("\n[17] Resume-from-disk and ETA")
    import os
    import tempfile
    import libtorrent as lt
    from services.torrent_service import TorrentDownloader, downloads_store
    import server as server_mod
    from web.routes import torrents

    MAGNET = "magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567&dn=TestFilm"
    HASH = "0123456789abcdef0123456789abcdef01234567"

    check("info hash is extracted from a magnet URI",
          TorrentDownloader._info_hash_from_magnet(MAGNET) == HASH,
          TorrentDownloader._info_hash_from_magnet(MAGNET))
    check("a non-magnet string yields no hash",
          TorrentDownloader._info_hash_from_magnet("http://example.com/x.torrent") is None)

    d = tempfile.mkdtemp()
    dl = object.__new__(TorrentDownloader)
    dl.lt = lt
    dl.resume_dir = d
    dl.download_dir = d
    dl.movie_target = d
    dl.tv_target = d

    # Real libtorrent session so this exercises the actual 2.x API, not a mock.
    params = lt.parse_magnet_uri(MAGNET)
    params.save_path = d
    dl.session = lt.session()
    handle = dl.session.add_torrent(params)

    check("resume data is flushed to disk", dl._flush_handle_resume(handle) is True)
    files = [f for f in os.listdir(d) if f.endswith(".ltresume")]
    check("a .ltresume file is written", files == [f"{HASH}.ltresume"], files)

    loaded = dl._load_resume_params(MAGNET)
    check("resume data loads back", loaded is not None, type(loaded).__name__)
    loaded.save_path = d
    fresh = lt.session()
    check("resumed params can be added to a brand new session",
          fresh.add_torrent(loaded).name() == "TestFilm")

    # A cold start (no file) must work, not raise.
    check("cold start with no resume file returns None",
          dl._load_resume_params(
              "magnet:?xt=urn:btih:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa&dn=Other") is None)

    # A corrupt file must degrade to a fresh download, never block one.
    with open(os.path.join(d, f"{HASH}.ltresume"), "wb") as fh:
        fh.write(b"garbage")
    check("corrupt resume file degrades gracefully", dl._load_resume_params(MAGNET) is None)

    # ETA is computed from remaining bytes / current rate.
    downloads_store[1] = {
        "movie_id": 1, "status": "downloading", "progress": 42.5,
        "download_speed": 1_048_576, "total_size": 10_737_418_240,
        "downloaded": 4_563_984_768, "eta_seconds": 5887, "resumed": True,
        "peers": 12, "seeds": 33, "error": None,
    }
    remaining = 10_737_418_240 - 4_563_984_768
    check("eta = remaining / speed",
          abs(5887 - remaining / 1_048_576) < 2, round(remaining / 1_048_576))
    downloads_store.pop(1, None)

    # ETA / resume / swarm fields must survive the status endpoint.
    downloads_store[2] = {
        "movie_id": 2, "status": "downloading", "progress": 10.0,
        "download_speed": 2048, "total_size": 1000, "downloaded": 100,
        "eta_seconds": 440, "resumed": True, "peers": 3, "seeds": 9, "error": None,
    }
    import asyncio
    payload = asyncio.run(torrents.get_torrent_status(2))
    for field in ("eta_seconds", "resumed", "peers", "seeds"):
        check(f"status endpoint exposes {field}", field in payload, sorted(payload))
    downloads_store.pop(2, None)


def test_origin_intent(build):
    print("\n[18] Origin-of-cinema requests (South Asian, Korean, ...)")
    from services.recommender_service import parse_origin_intent
    db = build()

    spec = parse_origin_intent("show me south asian movies")
    check("'south asian' is recognised as an origin request", spec is not None, spec)
    check("maps to India (+ neighbours)", "IN" in spec["countries"], spec["countries"])
    check("includes the South Asian language codes",
          {"hi", "ta", "te", "ml", "kn"} <= set(spec["languages"]), spec["languages"])
    check("a plain genre query is not an origin request",
          parse_origin_intent("a funny film") is None)
    check("no query is not an origin request", parse_origin_intent(None) is None)
    check("korean maps to ko", "ko" in parse_origin_intent("korean drama")["languages"])

    # Every phrase the "From" chips can emit must parse. The chip labels live
    # in server.py and the intents here; when those two lists drifted, 15
    # chips shipped with no parser entry and silently returned nothing.
    chip_phrases = ["south asian", "east asian", "latin american", "korean",
                    "indian", "japanese", "chinese", "spanish language",
                    "french", "german", "italian", "portuguese", "nigerian",
                    "iranian", "egyptian", "turkish", "russian", "bangladeshi",
                    "pakistani", "sri lankan", "nepali", "thai", "indonesian",
                    "filipino", "vietnamese", "greek", "swedish", "danish",
                    "norwegian", "finnish", "dutch", "polish", "ukrainian",
                    "argentine", "colombian", "chilean", "arabic"]
    unparsed = [ph for ph in chip_phrases if parse_origin_intent(ph) is None]
    check("every UI origin chip has a matching parser intent",
          not unparsed, unparsed)

    # Every country name a user might type must resolve. "mexican movies"
    # parsed as NO origin intent, so no filter ran and it returned American
    # Graffiti and Straw Dogs — the precise failure this feature prevents,
    # resurfacing for any country missing from the table. No hardcoded list can
    # stay in sync forever, so assert the invariant instead: every ORIGIN_INTENT
    # key must round-trip through the parser.
    from services.recommender_service import ORIGIN_INTENT
    self_parse_fail = [k for k in ORIGIN_INTENT
                       if parse_origin_intent(k) is None]
    check("every declared origin intent parses from its own key",
          not self_parse_fail, self_parse_fail)
    for country in ("MX", "PE", "CL", "AR", "CO", "BR"):
        covered = any(country in spec["countries"]
                      for spec in ORIGIN_INTENT.values())
        check(f"Latin American country {country} is addressable",
              covered, sorted(ORIGIN_INTENT))
    # Longer, more specific phrases must win over shorter ones.
    check("the more specific phrase wins",
          "hi" in parse_origin_intent("bollywood")["languages"]
          and "ko" not in parse_origin_intent("bollywood")["languages"])

    # SQL: country AND language, so TMDB origin mislabels don't leak through.
    with db.get_connection() as conn:
        for title, country, lang in [
            ("Real Hindi", "IN", "hi"), ("Real Tamil", "IN", "ta"),
            ("Hollywood Labeled IN", "IN", "en"),   # TMDB mislabel
            ("Korean Film", "KR", "ko"),
        ]:
            conn.execute(
                "INSERT INTO media_items (title, release_year, media_type, rating, "
                "vote_count, genres, origin_country, original_language) "
                "VALUES (?, 2020, 'movie', 7.8, 9000, 'Drama', ?, ?);",
                (title, country, lang))

    sa = db.get_top_candidates(media_type="movie", origin_countries=["IN"],
                               origin_languages=["hi", "ta"],
                               exclude_watched=False, min_rating=6.0, limit=100)
    titles = {c["title"] for c in sa}
    check("origin filter returns the matching films",
          {"Real Hindi", "Real Tamil"} <= titles, sorted(titles))
    check("language requirement excludes a mislabelled Hollywood title",
          "Hollywood Labeled IN" not in titles, sorted(titles))
    check("another region is excluded", "Korean Film" not in titles, sorted(titles))

    # With no language requirement, the country filter still works on its own.
    by_country = db.get_top_candidates(media_type="movie", origin_countries=["IN"],
                                       exclude_watched=False, min_rating=6.0, limit=100)
    check("country-only filter includes IN titles",
          "Hollywood Labeled IN" in {c["title"] for c in by_country},
          sorted(c["title"] for c in by_country))

    # End to end through the recommender.
    from services.recommender_service import RecommenderService
    r = RecommenderService(db, shortlist_size=50, final_pool=20)
    out = r.recommend(mood_id=8, media_type="movie", query="south asian movies",
                      profile="together", page=1, page_size=10)
    langs = {i.get("original_language") for i in out["items"]}
    check("recommender returns only South Asian films for that query",
          langs and langs <= {"hi", "ta", "te", "ml", "kn", "bn", "pa", "mr", "gu", "ur"},
          sorted(x for x in langs if x))


def test_negated_genre_ranking(build):
    print("\n[17b] 'no animation' removes animation and boosts nothing animated")
    from services.recommender_service import RecommenderService
    db = build()
    with db.get_connection() as conn:
        for i in range(30):
            conn.execute(
                "INSERT INTO media_items (title, release_year, media_type, rating, vote_count, genres, overview) "
                "VALUES (?, 2020, 'movie', 8.0, 9000, 'Animation, Comedy', 'A funny animation.');",
                (f"Cartoon {i}",))
            conn.execute(
                "INSERT INTO media_items (title, release_year, media_type, rating, vote_count, genres, overview) "
                "VALUES (?, 2020, 'movie', 7.0, 9000, 'Comedy', 'A funny story.');",
                (f"Live {i}",))
    rec = RecommenderService(db)
    out = rec.recommend(mood_id=8, query="something funny tonight, no animation", page_size=50)
    titles = [i["title"] for i in out["items"]]
    check("no animated film comes back", titles and not any(t.startswith("Cartoon") for t in titles),
          titles[:5])


def test_shown_memory(build):
    print("\n[17d] Discover remembers what it showed and nobody opened")
    from services.recommender_service import RecommenderService
    db = build()
    with db.get_connection() as conn:
        conn.execute("DELETE FROM media_items;")
        for i in range(40):
            conn.execute(
                "INSERT INTO media_items (title, release_year, media_type, rating, vote_count, genres) "
                "VALUES (?, 2020, 'movie', ?, 9000, 'Drama');", (f"Film {i}", 8.5 - i * 0.02))
    rec = RecommenderService(db, shortlist_size=100, final_pool=40)
    first = rec.recommend(mood_id=8, page_size=10, remember_shown=True)["items"]
    sid = db.create_discover_session(profile="together", media_type="movie", query_text=None, request={}, engine="t")
    db.log_impressions(sid, first, 1)
    again = rec.recommend(mood_id=8, page_size=10, remember_shown=True)["items"]
    check("the next search leads with films not shown last time",
          not ({i["id"] for i in first} & {i["id"] for i in again}),
          ([i["title"] for i in first[:3]], [i["title"] for i in again[:3]]))
    paging = rec.recommend(mood_id=8, page_size=10, remember_shown=True, skip_session_id=sid)["items"]
    check("paging the same session is not penalised by its own page",
          [i["id"] for i in paging] == [i["id"] for i in first])
    off = rec.recommend(mood_id=8, page_size=10)["items"]
    check("memory is off unless asked for", [i["id"] for i in off] == [i["id"] for i in first])
    db.record_outcome(first[0]["id"], "opened", sid)
    db.record_outcome(first[1]["id"], "dismissed", sid)
    pool = rec.recommend(mood_id=8, remember_shown=True, return_pool=True)["pool"]
    rank = {it["id"]: n for n, it in enumerate(pool)}
    check("an opened film is not pushed down", rank[first[0]["id"]] == 0, rank[first[0]["id"]])
    check("a dismissed film drops to the bottom", rank[first[1]["id"]] == len(pool) - 1, rank[first[1]["id"]])


def test_genre_spread(build):
    print("\n[17e] No shelf fills a page unless asked")
    from services.recommender_service import apply_genre_spread
    items = [{"id": i, "genres": "Animation, Family" if i < 8 else "Drama"} for i in range(20)]
    out = apply_genre_spread(items)
    check("at most 2 animated in the first 10", sum(1 for x in out[:10] if "Animation" in x["genres"]) == 2,
          [x["id"] for x in out[:10]])
    check("nothing is dropped", sorted(x["id"] for x in out) == list(range(20)))
    check("the best film still leads", out[0]["id"] == 0)
    asked = apply_genre_spread(items, {"Animation", "Family"})
    check("an asked-for genre is left alone", [x["id"] for x in asked] == list(range(20)))


def test_averaged_taste(build):
    print("\n[17f] Averaged taste: no cap, no snowball, no multi-genre inflation")
    from services.recommender_service import averaged_taste, build_taste_affinity
    rows = ([{"genres": "Animation, Family", "tier2_keywords": "", "score": 0.3}] * 20
            + [{"genres": "Drama", "tier2_keywords": "", "score": 0.6}] * 5
            + [{"genres": "Romance", "tier2_keywords": "", "score": -0.3}] * 5)
    aff = build_taste_affinity(rows)
    drama = averaged_taste({"drama"}, set(), aff)
    anim = averaged_taste({"animation", "family"}, set(), aff)
    check("many middling ratings of a genre don't beat a few loved ones", drama > anim, (drama, anim))
    check("a disliked genre scores negative", averaged_taste({"romance"}, set(), aff) < 0)
    many = averaged_taste({"drama", "animation", "family", "adventure", "comedy"}, set(), aff)
    check("more genres don't add up", many < drama, (many, drama))
    one = build_taste_affinity([{"genres": "Horror", "tier2_keywords": "", "score": 0.7}])
    check("a single rating already gives a signal", averaged_taste({"horror"}, set(), one) > 0)


def test_negated_origin(build):
    print("\n[17c] A negated place is not a request for it")
    from services.recommender_service import parse_origin_intent
    check("'no anime' is not an anime request", parse_origin_intent("funny, no anime") is None)
    check("'anything but korean' is not a Korean request",
          parse_origin_intent("anything but korean") is None)
    check("'korean movies' still is", (parse_origin_intent("korean movies") or {}).get("label") == "Korean")


def test_small_cinema_ranking(build):
    """Regression tests for ranking a small, low-vote national cinema.

    All three bugs here were invisible on a mainstream catalogue: TMDB's
    Bangladeshi titles average 3.7 votes against 822 across the whole
    catalogue, so every threshold and prior tuned on Hollywood silently
    flattened an entire national cinema.
    """
    print("\n[19] Small/low-vote cinema ranking")
    db = build()
    from services.recommender_service import calculate_weighted_rating

    # A fixed 1000-vote prior pins a 190-vote film to the 6.5 global mean.
    flat = calculate_weighted_rating(8.5, 190, min_votes=1000)
    check("a fixed prior flattens even a well-reviewed small-cinema film",
          abs(flat - 6.5) < 0.6, round(flat, 2))
    # A pool-derived prior lets real signal through.
    scaled = calculate_weighted_rating(8.5, 190, min_votes=20)
    check("a pool-scaled prior preserves the rating signal",
          scaled > 8.0, round(scaled, 2))

    # The pool prior is derived from the candidates actually being ranked.
    titles = [("Megher Kobita", 8.9, 17), ("Utshob", 8.9, 17), ("Onnoroni",
              9.4, 11), ("Fagun Haway", 7.4, 33), ("Shankhonad", 7.7, 21)]
    rows = []
    for i, (t, r, v) in enumerate(titles):
        rows.append({"media_type": "movie", "tmdb_id": 90000 + i, "title": t,
                     "rating": r, "vote_count": v, "genres": "Drama",
                     "origin_country": "BD", "original_language": "bn"})
    db.upsert_catalogue_items(rows)

    out = db.get_top_candidates(media_type="movie", origin_countries=["BD"],
                               origin_languages=["bn"], exclude_watched=False,
                               min_rating=0.0, limit=50)
    names = [c["title"] for c in out]
    check("all low-vote titles are still returned", len(names) == len(titles), names)
    # Exact Bayesian order at a pool median of 17 votes puts the ~7.4-7.7
    # titles last; the key property is that the pool-derived prior separates
    # them at all. Under the old fixed prior of 1000 every one of these five
    # collapsed into a 0.02-wide band, making order effectively arbitrary.
    spread = {}
    for t, rr, vv in titles:
        spread[t] = (vv / (vv + 17)) * rr + (17 / (vv + 17)) * 6.5
    worst_two = sorted(spread, key=lambda k: spread[k])[:2]
    check("the two weakest titles rank last, not first",
          set(worst_two) == set(names[-2:]), names)
    best = max(spread, key=lambda k: spread[k])
    check("the strongest title outranks the weakest",
          names.index(best) < names.index(worst_two[0]), names)
    check("vote counts are persisted, not left at 0",
          all(c["vote_count"] > 0 for c in out),
          [c["vote_count"] for c in out])

    # Targeted queries must not be randomly reshuffled after scoring.
    from services.recommender_service import RecommenderService
    r = RecommenderService(db, shortlist_size=50, final_pool=50)
    got = r.recommend(mood_id=8, media_type="movie", query="bangladeshi movies",
                      profile="me", page=1, page_size=10, variety=True)
    got_names = [i["title"] for i in got["items"]]
    check("an origin query is not shuffled away from the quality order",
          got_names[-1] in ("Fagun Haway", "Shankhonad"), got_names)
    # Repeat calls must be stable: a targeted query is not reshuffled.
    again = r.recommend(mood_id=8, media_type="movie", query="bangladeshi movies",
                        profile="me", page=1, page_size=10, variety=True)
    check("a targeted query returns a stable order across calls",
          [i["title"] for i in again["items"]] == got_names,
          [i["title"] for i in again["items"]])


def test_concept_intent(build):
    """A request about an IDEA, not a genre.

    "Something like They Live — chill, a bit funny, but a great concept to
    interpret" returned a Mario cartoon. Three separate causes: the request
    had no concept signal at all, "chill" mapped to Family/Animation/Music,
    and the generative engine (which knows nothing of premise) served it.
    """
    print("\n[20] Concept-driven requests")
    db = build()
    from services.recommender_service import (
        CONCEPT_INCOMPATIBLE_GENRES, THEMES, parse_query_intent,
        resolve_effective_genres,
    )
    Q = ('we would like to watch something cool like "they live". it was chill '
         'but it was such a great concept up to interpret, it was funny but it '
         'showed those robotic people among us, like people who use others')

    intent = parse_query_intent(Q)
    check("a 'great concept to interpret' request is detected",
          intent["concept_requested"] is True, intent["matched_terms"])
    check("a plain funny request is not treated as a concept request",
          parse_query_intent("something funny")["concept_requested"] is False)
    check("no query is not a concept request",
          parse_query_intent(None)["concept_requested"] is False)

    # The concept widening must not resurrect a genre the user excluded.
    spec = resolve_effective_genres(
        ["Animation", "Family", "Comedy", "Romance", "Music"], None, Q)
    check("no genre is both required and forbidden",
          not (set(spec["include"]) & set(spec["exclude"])),
          (spec["include"], spec["exclude"]))
    check("an explicit exclusion survives the concept widening",
          "Horror" in spec["exclude"], spec["exclude"])
    check("a concept request is not narrowed by the mood's genres (They Live is sci-fi)",
          spec["include"] == [], spec["include"])

    # Theme terms are premise phrases, not genre words.
    text = ("aliens are systematically gaining control of the Earth by "
            "masquerading as humans")
    hits = {n for n, s in THEMES.items() if any(t in text for t in s["terms"])}
    check("a covert-alien-control premise is recognised",
          "hidden_puppeteer" in hits, hits)
    check("a generic business premise does not match 'people used by others'",
          "exploitation" not in {
              n for n, s in THEMES.items()
              if any(t in "a ceo fights for control of his company"
                     for t in s["terms"])})

    # End to end: a concept request must not return a family cartoon.
    rows = []
    fixtures = [
        ("Deep Alien Control", "A defence lawyer discovers aliens are "
         "masquerading as humans and lulling the public into submission.",
         "Science Fiction, Thriller", 7.3, 3662),
        ("Family Cartoon Adventure", "A plumber rescues a princess from an "
         "evil turtle in a bright cartoon world.", "Animation, Family, Comedy",
         7.6, 3000),
        # Rated equal to the premise film on purpose: the only thing that can
        # separate them is the concept signal. A higher rating here would make
        # the test pass for the wrong reason.
        ("Corporate Drama", "A ruthless executive fights for control of his "
         "company.", "Drama", 7.3, 2500),
    ]
    for i, (t, ov, g, rt, vc) in enumerate(fixtures):
        rows.append({"media_type": "movie", "tmdb_id": 91000 + i, "title": t,
                     "overview": ov, "genres": g, "rating": rt,
                     "vote_count": vc})
    db.upsert_catalogue_items(rows)

    # Isolated catalogue: the shared fixture already holds Interstellar, Se7en
    # and Inception, which are excellent films and would outrank anything added
    # here. This test is about premise ranking, not about those three.
    import tempfile
    from services.recommender_service import RecommenderService
    solo = DatabaseManager(tempfile.mktemp(suffix=".db"))
    solo.upsert_catalogue_items(rows)
    r = RecommenderService(solo, shortlist_size=50, final_pool=50)
    out = r.recommend(mood_id=8, media_type="movie", query=Q, profile="me",
                      page=1, page_size=10)
    titles = [i["title"] for i in out["items"]]
    check("the premise-relevant film outranks the family cartoon",
          titles and titles[0] == "Deep Alien Control", titles)
    # The cartoon is rated HIGHER than the premise film, so the only reason it
    # can be demoted is the concept layer. That is the regression this guards.
    check("a higher-rated family cartoon is still demoted for a concept request",
          titles.index("Deep Alien Control") < titles.index("Family Cartoon Adventure"),
          titles)
    themed = [i for i in out["items"] if i.get("matched_themes")]
    check("matched themes are reported back to the UI",
          any("hidden controllers among us" in (i.get("matched_themes") or [])
              for i in themed),
          [i.get("matched_themes") for i in themed])

    # The generative engine cannot evaluate premise, so it must not serve these.
    from services.generative_retriever import route_recommend
    served = route_recommend(r, None, "generative_only", query=Q)
    check("a concept request is routed away from the generative engine",
          served.get("query_routed_to") == "legacy", served.get("engine"))


def main():
    tmpdir = tempfile.mkdtemp(prefix="ratings_test_")
    counter = [0]

    def build():
        """Fresh, isolated catalogue per test that needs one."""
        counter[0] += 1
        return build_db(os.path.join(tmpdir, f"test{counter[0]}.db"))

    try:
        db = build()
        test_migration(db)
        test_not_seen(db)
        test_tiers(db)
        test_upsert(db)
        test_shrinkage(db)
        test_queue(build)
        test_co_view_history(db)
        test_validation(db)
        test_genre_sql(build)
        test_profile_scoring(build)
        test_balanced_queue(build)
        test_query_intent(build)
        test_genre_exclusion_sql(build)
        test_pagination_shape(build)
        test_rating_genre_endpoint(build)
        test_download_survives_refresh(build)
        test_resume_and_eta(build)
        test_origin_intent(build)
        test_negated_genre_ranking(build)
        test_negated_origin(build)
        test_shown_memory(build)
        test_genre_spread(build)
        test_averaged_taste(build)
        test_small_cinema_ranking(build)
        test_concept_intent(build)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    print("\n" + "=" * 60)
    if failures:
        print(f"❌ {len(failures)} FAILED: {failures}")
        sys.exit(1)
    print("✅ All co-view rating tests passed.")


if __name__ == "__main__":
    main()