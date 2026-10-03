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
    check("duplicate name rejected", not db.update_profile_name(2, "Me"))
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
    check("single-profile views invert the weights",
          PROFILE_MIX["me"][0] > PROFILE_MIX["me"][1]
          and PROFILE_MIX["partner"][1] > PROFILE_MIX["partner"][0], PROFILE_MIX)

    # Ranking: profile-aware path engages and me != partner.
    r = RecommenderService(db, shortlist_size=50, final_pool=20)
    together = r.recommend(mood_id=8, media_type="movie", profile="together", page=1, page_size=10)
    check("profile-aware scoring used (tier field present)",
          "co_view_tier" in together["items"][0], list(together["items"][0].keys())[:6])

    me = r.recommend(mood_id=8, media_type="movie", profile="me", page=1, page_size=10)
    him = r.recommend(mood_id=8, media_type="movie", profile="partner", page=1, page_size=10)
    check("'me' and 'partner' are NOT the same list",
          [i["id"] for i in me["items"]] != [i["id"] for i in him["items"]])
    check("own/other taste bonuses actually differ",
          any(i["taste_bonus_own"] != i["taste_bonus_other"] for i in me["items"]),
          [(i["taste_bonus_own"], i["taste_bonus_other"]) for i in me["items"][:3]])
    check("films the viewer already rated are excluded",
          all(i["id"] not in (1, 3, 4) for i in me["items"]),
          [i["id"] for i in me["items"]])

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
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    print("\n" + "=" * 60)
    if failures:
        print(f"❌ {len(failures)} FAILED: {failures}")
        sys.exit(1)
    print("✅ All co-view rating tests passed.")


if __name__ == "__main__":
    main()