"""
Offline TIGER generative retriever tests (tests/test_generative_retriever.py)

Pure-Python: no network, no torch, no onnxruntime, no database.
Run standalone:

    uv run python tests/test_generative_retriever.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

failures = []


def check(label, cond, detail=""):
    if cond:
        print(f"  ✅ {label}")
    else:
        print(f"  ❌ {label} {detail}")
        failures.append(label)


from services.generative_retriever import (  # noqa: E402
    InsufficientHistoryError,
    GenerativeRetriever,
    beam_decode_top_tuples,
    load_prefix_trie,
    route_recommend,
    trie_lookup,
)

import numpy as np  # noqa: E402

# ---------------------------------------------------------------------------
# 1. Prefix trie: build, lookup, collisions, unknown tuples
# ---------------------------------------------------------------------------
print("1. Prefix trie")
semantic_ids = {
    "num_levels": 3,
    "items": {
        "101": [5, 9, 200],
        "102": [5, 9, 200, 0],   # collision group -> leaf tokens
        "103": [5, 9, 200, 1],
        "104": [7, 0, 1],
    },
}
trie = load_prefix_trie(semantic_ids)

check("trie build", isinstance(trie, dict) and "5" in trie)
check("unique tuple lookup", trie_lookup(trie, [7, 0, 1]) == [104])
check("collision group resolves to all items",
      sorted(trie_lookup(trie, [5, 9, 200])) == [101, 102, 103])
check("unknown tuple -> empty (no hallucination)", trie_lookup(trie, [1, 2, 3]) == [])
check("short tuple -> empty", trie_lookup(trie, [5]) == [])

# ---------------------------------------------------------------------------
# 2. Beam decode
# ---------------------------------------------------------------------------
print("2. Beam decoding")
logits = np.zeros((3, 256))
logits[0, 10] = 5.0
logits[1, 20] = 4.0
logits[2, 30] = 3.0
top = beam_decode_top_tuples(logits, top_k=5)
check("argmax tuple first", top[0][0] == (10, 20, 30), detail=str(top[:1]))
check("deterministic greedy when confident", len(top) == 5)

# ---------------------------------------------------------------------------
# 3. GenerativeRetriever sequence handling (mock DB, no artifacts)
# ---------------------------------------------------------------------------
print("3. Sequence conversion & history handling")


class FakeDB:
    def __init__(self, history):
        self._history = history

    def get_watch_history_chronological(self, limit=100):
        return self._history

    def get_candidates(self, **kwargs):
        return []

    def get_user_taste_profile(self, min_rating=7.0):
        return {"preferred_genres": {}, "preferred_keywords": {}}


fake_rows = [
    {"media_item_id": 101},
    {"media_item_id": 101},  # consecutive duplicate -> collapsed
    {"media_item_id": 104},
]
ret = GenerativeRetriever(FakeDB(fake_rows))
ret._semantic_ids = {str(k): v for k, v in semantic_ids["items"].items()}
ids = ret.get_history_item_ids()
check("history ids chronological + deduped", ids == [101, 104], detail=str(ids))
seq = ret.history_to_semantic_sequence(ids)
check("history -> semantic triplets", seq == [[5, 9, 200], [7, 0, 1]], detail=str(seq))

ret_small = GenerativeRetriever(FakeDB([{"media_item_id": 104}]))
ret_small._semantic_ids = ret._semantic_ids
try:
    ret_small.recommend()
    check("insufficient history raises", False)
except InsufficientHistoryError:
    check("insufficient history raises", True)

# unknown item in history is skipped
ret_unknown = GenerativeRetriever(FakeDB([{"media_item_id": 999}, {"media_item_id": 101}]))
ret_unknown._semantic_ids = ret._semantic_ids
check("unknown history items skipped",
      ret_unknown.history_to_semantic_sequence([999, 101]) == [[5, 9, 200]])

# ---------------------------------------------------------------------------
# 4. Feature-flag router (mock engines, no artifacts)
# ---------------------------------------------------------------------------
print("4. Feature-flag router")


class MockLegacy:
    def __init__(self):
        self.calls = 0

    def recommend(self, **kwargs):
        self.calls += 1
        return {"total_results": 1, "page": kwargs.get("page", 1),
                "page_size": kwargs.get("page_size", 10),
                "has_more": False, "items": [{"title": "Legacy"}]}


class MockGenReady:
    """Generative engine that always succeeds."""

    def is_ready(self):
        return True

    def recommend(self, **kwargs):
        return {"total_results": 1, "page": kwargs.get("page", 1),
                "page_size": kwargs.get("page_size", 10),
                "has_more": False, "items": [{"title": "Gen"}]}


class MockGenBroken:
    """Generative engine present but failing at runtime (fallback safety)."""

    def is_ready(self):
        return True

    def recommend(self, **kwargs):
        raise InsufficientHistoryError("boom")


class MockGenAbsent:
    def is_ready(self):
        return False

    def recommend(self, **kwargs):  # pragma: no cover
        raise AssertionError("should not be called")


legacy = MockLegacy()
r = route_recommend(legacy, MockGenAbsent(), "fallback", mood_id=8)
check("fallback mode + no artifacts -> legacy",
      r["engine"] == "legacy" and r["items"][0]["title"] == "Legacy" and legacy.calls == 1)

r = route_recommend(MockLegacy(), MockGenReady(), "generative_only", mood_id=8)
check("generative_only -> generative", r["engine"] == "generative" and r["items"][0]["title"] == "Gen")

legacy2 = MockLegacy()
r = route_recommend(legacy2, MockGenReady(), "legacy_only", mood_id=8)
check("legacy_only kill-switch", r["engine"] == "legacy" and legacy2.calls == 1)

legacy3 = MockLegacy()
r = route_recommend(legacy3, MockGenBroken(), "generative_only", mood_id=8)
check("runtime failure -> legacy fallback",
      r["engine"] == "legacy" and legacy3.calls == 1)

legacy4 = MockLegacy()
r = route_recommend(legacy4, MockGenReady(), "generative_only", mood_id=8, query="funny, no animation")
check("a typed 'no animation' goes to legacy (generative ignores exclusions)",
      r["engine"] == "legacy" and legacy4.calls == 1 and r.get("query_routed_to") == "legacy")

# kwargs forwarding through the router
r = route_recommend(MockLegacy(), MockGenReady(), "fallback", mood_id=8, page=2, page_size=5)
check("kwargs forwarded", r["page"] == 2)

# ---------------------------------------------------------------------------
# 5. Artifact-missing readiness (real filesystem, no models dir needed)
# ---------------------------------------------------------------------------
print("5. Artifact readiness")
missing = GenerativeRetriever(FakeDB([]), config={})
missing.artifact_dir = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "models", "tiger_definitely_missing",
)
check("missing artifacts -> not ready", missing.is_ready() is False)

# ---------------------------------------------------------------------------
print()
if failures:
    print(f"❌ {len(failures)} check(s) failed: {failures}")
    sys.exit(1)
print("✅ All offline generative retriever checks passed")


