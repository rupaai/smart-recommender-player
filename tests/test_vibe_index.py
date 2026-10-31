"""
Vibe engine tests (tests/test_vibe_index.py)

No model needed: a fake index with hand-made vectors stands in for the
bge-small build, so this runs on the Pi and in CI.

    uv run python tests/test_vibe_index.py
"""

import os
import shutil
import sqlite3
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import DatabaseManager  # noqa: E402
from services.recommender_service import RecommenderService, parse_origin_intent, parse_query_intent  # noqa: E402
from services.vibe_index import VibeIndex, like_phrases, resolve_like_titles  # noqa: E402
from services.catalogue_search import CatalogueSearch  # noqa: E402
import training.build_vibe_index as build  # noqa: E402

failures = []


def check(label, condition, detail=""):
    if condition:
        print(f"  ✅ {label}")
    else:
        print(f"  ❌ {label} — got: {detail}")
        failures.append(label)


def unit(v):
    v = np.asarray(v, dtype=np.float32)
    return v / np.linalg.norm(v)


class FakeIndex(VibeIndex):
    """Vectors set by hand; the 'encoder' maps known texts to vectors."""

    def __init__(self, ids, vectors, texts):
        super().__init__(root="/nonexistent")
        self.ids, self.vectors = list(ids), np.stack([unit(v) for v in vectors])
        self.row = {i: r for r, i in enumerate(self.ids)}
        self.texts = texts
        self.config = {"dials": ["energy"], "facet_quantiles": {"energy": list(np.linspace(-1, 1, 101))}}
        self.anchors = np.stack([unit([1, 0, 0, 0])])

    def is_ready(self):
        return True

    def encode_query(self, text):
        return unit(self.texts.get(text, [0, 0, 0, 1]))


def test_index():
    print("Index")
    idx = FakeIndex([10, 11, 12], [[1, 0, 0, 0], [0, 1, 0, 0], [0.9, 0.1, 0, 0]], {"calm": [1, 0, 0, 0]})
    v = idx.encode_query("calm")
    top = idx.top(v, 2)
    check("nearest first", [i for i, _ in top] == [10, 12], top)
    check("exclude skips ids", [i for i, _ in idx.top(v, 2, exclude=[10])] == [12, 11])
    sims = idx.similarities(v, [11, 99])
    check("ids without a vector are absent", set(sims) == {11})
    blended = idx.query_vector("calm", like_ids=[11])
    check("'like' pulls the query towards the named film", 0.6 < float(blended @ unit([0, 1, 0, 0])) < 0.8)
    check("facet projection 0-1", idx.facets_for(unit([1, 0, 0, 0]))["energy"] == 1.0)
    check("not ready without files", VibeIndex(root="/nonexistent").is_ready() is False)


def test_like_and_parsers():
    print("'Like' titles and parser fixes")
    check("title after like", like_phrases("something like Arrival but funnier") == ["Arrival"])
    check("ordinary 'like' ignored", like_phrases("I'd like a film, feel like watching something") == [])
    check("'not too complicated' keeps the exclusions",
          "Horror" in parse_query_intent("not too complicated")["exclude_genres"])
    check("'not complicated' no longer adds Horror",
          "Horror" not in parse_query_intent("not complicated")["include_genres"])
    check("negation reaches 3 words back", "Horror" in parse_query_intent("no really scary films")["exclude_genres"])
    check("negation stops at 'but'", "Comedy" in parse_query_intent("not scary but funny")["boost_genres"])
    check("'indiana jones' is not Indian", parse_origin_intent("indiana jones vibes") is None)
    check("'animated' is not anime", parse_origin_intent("an animated film") is None)
    check("'polished' is not Polish", parse_origin_intent("something polished") is None)
    check("plural still works", parse_origin_intent("korean films")["label"])


def seed(path):
    db = DatabaseManager(path)
    with db.get_connection() as conn:
        rows = [("Arrival", "Science Fiction, Drama", 8.0, 9000), ("Calm Film", "Drama", 6.6, 300),
                ("Loud Film", "Action", 7.9, 8000), ("Gentle Drama", "Drama", 6.9, 500),
                ("Big Hit", "Action", 8.3, 20000)]
        for i, (t, g, r, v) in enumerate(rows, start=1):
            conn.execute("INSERT INTO media_items (id, media_type, tmdb_id, title, genres, rating, vote_count, overview)"
                         " VALUES (?, 'movie', ?, ?, ?, ?, ?, 'x')", (i, 100 + i, t, g, r, v))
    return db


def test_recommender(tmp):
    print("Recommender with vibe")
    db = seed(os.path.join(tmp, "v.db"))
    idx = FakeIndex([1, 2, 3, 4, 5],
                    [[0, 1, 0, 0], [1, 0, 0, 0], [0, 0, 1, 0], [0.95, 0.05, 0, 0], [0, 0, 0.9, 0.1]],
                    {"something calm": [1, 0, 0, 0], "like Arrival": [0, 0, 0, 1]})
    rec = RecommenderService(db, vibe_index=idx, shortlist_size=2)
    out = rec.recommend(query="something calm", min_rating_override=0, page_size=5, profile="me", variety=True)
    titles = [it["title"] for it in out["items"]]
    check("vibe used", out["vibe"]["used"] is True, out.get("vibe"))
    check("calm films join a quality shortlist of 2 and lead", titles[:2] == ["Calm Film", "Gentle Drama"], titles)
    check("score parts carry the vibe boost", out["items"][0].get("vibe_boost") == 3.0, out["items"][0])

    out = rec.recommend(query="like Arrival", min_rating_override=0, page_size=5, profile="me")
    check("named film resolved", out["vibe"]["like_titles"] == ["Arrival"], out["vibe"])
    check("named film not suggested back", "Arrival" not in [it["title"] for it in out["items"]])

    plain = RecommenderService(db, shortlist_size=2).recommend(query="something calm", min_rating_override=0,
                                                              profile="me")
    check("without an index nothing changes", plain["vibe"]["used"] is False
          and [it["title"] for it in plain["items"]] == ["Big Hit", "Arrival"], plain["items"])

    found = resolve_like_titles("a film like calm film", db, CatalogueSearch(db))
    check("a named title is found by its exact name", found and found[0]["id"] == 2)


def test_build_helpers(tmp):
    print("Build helpers (numpy only)")
    rng = np.random.default_rng(0)
    centres = np.eye(8, 16, dtype=np.float32)
    x = np.concatenate([c + 0.05 * rng.standard_normal((50, 16)) for c in centres]).astype(np.float32)
    x /= np.linalg.norm(x, axis=1, keepdims=True)
    labels, cents = build.spherical_kmeans(x, 8)
    pure = all(len(set(labels[i * 50:(i + 1) * 50])) == 1 for i in range(8))
    check("k-means recovers separated groups", pure and len(set(labels)) == 8)
    check("k between 80 and 150 for the full catalogue", build.choose_k(50000) == 112 and build.choose_k(10 ** 6) == 150)
    terms = [["heist", "comedy"] if i < 50 else ["space"] for i in range(len(x))]
    info = build.name_clusters(labels, 8, terms)
    check("clusters named by their typical terms", info[labels[0]]["label"].startswith("heist"), info[labels[0]])
    anchors = np.stack([x[0]] * len(build.DIALS))
    quants = build.facet_quantiles(x @ anchors.T)
    llm = [{"energy": 0.9}] + [None] * (len(x) - 1)
    facets = build.compute_facets(x, anchors, quants, llm)
    check("LLM dial wins over the anchor value", facets[0]["energy"] == 0.9 and facets[0]["source"] == "mixed")
    check("anchor values are percentiles", 0 <= facets[1]["energy"] <= 1 and facets[1]["source"] == "anchor")

    path = os.path.join(tmp, "c.db")
    seed(path)
    conn = sqlite3.connect(path)
    ids = [1, 2, 3, 4, 5]
    lab = np.array([0, 0, 1, 1, 1])
    vec = np.eye(5, 4, dtype=np.float32)
    info = [{"size": 2, "top": ["drama"], "label": "drama"}, {"size": 3, "top": ["action"], "label": "action"}]
    build.write_clusters(conn, ids, lab, info, vec, np.eye(2, 4, dtype=np.float32), {})
    check("clusters stored", conn.execute("SELECT COUNT(*) FROM vibe_clusters").fetchone()[0] == 2)
    check("titles point at their cluster",
          conn.execute("SELECT vibe_cluster_id FROM media_items WHERE id = 3").fetchone()[0] == 2)
    conn.close()


if __name__ == "__main__":
    tmp = tempfile.mkdtemp()
    try:
        test_index()
        test_like_and_parsers()
        test_recommender(tmp)
        test_build_helpers(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{'ALL PASSED' if not failures else f'{len(failures)} FAILED'}")
    sys.exit(1 if failures else 0)
