"""Unit tests for the wishmatch core. Run: python packages/wishmatch/tests/test_core.py"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wishmatch import (  # noqa: E402
    DEFAULTS, Item, Reaction, Request, Vocabulary, Wish, Wishmatch, merged, mix_kinds, read_wish,
)
from wishmatch import candidates as cand  # noqa: E402
from wishmatch.explain import reasons as explain_reasons  # noqa: E402
from wishmatch.matcher import match  # noqa: E402
from wishmatch.profiles import Population, build_taste, combine  # noqa: E402
from wishmatch.types import Pick  # noqa: E402


# ---------------------------------------------------------------- fakes

VOCAB = Vocabulary(
    traits={
        "energy": {"low": "calm, slow", "high": "fast, wild"},
        "humour": {"low": "serious, grave", "high": "very funny, jokey"},
        "tension": {"low": "relaxed, safe", "high": "nail-biting, tense"},
        "gore": {"low": "bloodless", "high": "gory"},
    },
    tags={
        "feels": ["cozy", "witty", "silly", "tense", "eerie"],
        "good_for": ["a bad day", "date night"],
    },
)

RULES = {
    "candidates": {"total": 2000, "kinds": {"movie": 0.7, "tv": 0.3}},
    "mix": {"movie": 0.67, "tv": 0.33},
    "lexicon": {
        "traits": {
            "relaxing": {"energy": 0.2, "tension": 0.1},
            "funny": {"humour": 0.9},
            "scary": {"tension": 0.9},
            "gore": {"gore": 0.9},
        },
        "tags": {"make me laugh": ["silly", "witty"]},
        "hidden_gem_words": ["hidden gem"],
        "stop_words": ["a", "me", "us", "something"],
    },
}

LEX = merged(RULES)["lexicon"]


def _profile(i):
    """(traits, tags) for a repeating set of four kinds of item."""
    k = i % 4
    if k == 0:   # funny and cozy
        return ({"energy": 0.3, "humour": 0.9, "tension": 0.1, "gore": 0.0},
                {"cozy", "witty", "silly", "a bad day"})
    if k == 1:   # grim
        return ({"energy": 0.6, "humour": 0.1, "tension": 0.9, "gore": 0.8}, {"tense", "eerie"})
    if k == 2:   # middling
        return ({"energy": 0.5, "humour": 0.5, "tension": 0.5, "gore": 0.3}, {"cozy", "date night"})
    return ({"energy": 0.7, "humour": 0.2, "tension": 0.8, "gore": 0.3}, {"tense"})


def make_catalog():
    """30 items: 20 movies (14 well, 3 somewhat, 3 not known) and 10 tv (8, 1, 1)."""
    items = []
    for i in range(20):
        known = "well" if i < 14 else "somewhat" if i < 17 else "not"
        traits, tags = _profile(i)
        items.append(Item(id=f"m{i}", kind="movie", traits=traits, tags=tags, known=known,
                          popularity=10000 - i * 400, quality=0.5))
    for i in range(10):
        known = "well" if i < 8 else "somewhat" if i < 9 else "not"
        traits, tags = _profile(i)
        items.append(Item(id=f"t{i}", kind="tv", traits=traits, tags=tags, known=known,
                          popularity=5000 - i * 300, quality=0.5))
    return items


class FakeAdapter:
    def __init__(self, rules=None, reactions=None):
        self._rules = RULES if rules is None else rules
        self._items = make_catalog()
        self._by_id = {it.id: it for it in self._items}
        self._reactions = reactions or {}

    def vocabulary(self):
        return VOCAB

    def items(self, request):
        if request.only_ids is not None:
            return [self._by_id[i] for i in request.only_ids]
        return list(self._items)

    def describe(self, item_ids):
        return {i: self._by_id[i] for i in item_ids if i in self._by_id}

    def reactions(self, person):
        return list(self._reactions.get(person, []))

    def crowd(self, item_ids):
        return {}

    def rules(self):
        return RULES if self._rules is None else self._rules


# person 1 loves funny items and hates grim ones; person 2 is the opposite
REACTIONS = {
    1: [Reaction(f"m{i}", 1.0) for i in (0, 4, 8)] + [Reaction(f"m{i}", -1.0) for i in (1, 5, 9)],
    2: [Reaction(f"m{i}", -1.0) for i in (0, 4, 8)] + [Reaction(f"m{i}", 1.0) for i in (1, 5, 9)],
}


def approx(a, b, eps=1e-6):
    return abs(a - b) < eps


def mk(id_, kind="movie", known="well", pop=1000.0, **kw):
    return Item(id=id_, kind=kind, known=known, popularity=pop, **kw)


# ---------------------------------------------------------------- 1. read_wish

def test_read_wish_lexicon_tag_phrase():
    w = read_wish("make me laugh", VOCAB, LEX)
    assert w.tags == {"silly", "witty"}, w.tags
    assert w.words_unknown == []
    assert not w.avoid_tags and not w.traits


def test_read_wish_trait_word():
    w = read_wish("relaxing", VOCAB, LEX)
    assert approx(w.traits["energy"], 0.2) and approx(w.traits["tension"], 0.1)
    assert w.words_used == ["relaxing"]


def test_read_wish_negated_trait_flips():
    w = read_wish("not scary", VOCAB, LEX)
    assert approx(w.traits["tension"], 0.1), w.traits
    w = read_wish("no gore please", VOCAB, LEX)
    assert approx(w.traits["gore"], 0.1), w.traits
    assert "please" in w.words_unknown
    assert "no" not in w.words_unknown


def test_read_wish_negated_tag_goes_to_avoid():
    w = read_wish("something cozy but not eerie", VOCAB, LEX)
    assert w.tags == {"cozy"}, w.tags
    assert w.avoid_tags == {"eerie"}, w.avoid_tags
    assert "not eerie" in w.words_used


def test_read_wish_article_dropped_tag():
    w = read_wish("for bad day", VOCAB, LEX)
    assert w.tags == {"a bad day"}, w.tags
    w = read_wish("a bad day", VOCAB, LEX)
    assert w.tags == {"a bad day"}
    assert w.words_unknown == []


def test_read_wish_vocab_tag_and_longest_phrase():
    w = read_wish("date night", VOCAB, LEX)
    assert w.tags == {"date night"}
    # "make me laugh" is read as one phrase, not as the unknown words make/laugh
    assert read_wish("make me laugh", VOCAB, LEX).words_used == ["make me laugh"]


def test_read_wish_unknown_words():
    w = read_wish("something zebra relaxing", VOCAB, LEX)
    assert w.words_unknown == ["zebra"], w.words_unknown
    assert w.words_used == ["relaxing"]


def test_read_wish_hidden_gem():
    w = read_wish("a hidden gem", VOCAB, LEX)
    assert w.hidden_gems is True
    assert w.words_unknown == [], w.words_unknown
    assert read_wish("funny", VOCAB, LEX).hidden_gems is False


def test_read_wish_empty_and_none_text():
    assert read_wish("", VOCAB, LEX).is_empty()
    assert read_wish(None, VOCAB, LEX).is_empty()
    assert read_wish("funny", VOCAB).words_unknown == ["funny"]  # no lexicon


def test_read_wish_repeated_trait_is_averaged():
    w = read_wish("funny funny", VOCAB, LEX)
    assert approx(w.traits["humour"], 0.9)


def test_read_wish_ignores_traits_outside_vocabulary():
    lex = {"traits": {"spicy": {"heat": 1.0}}}
    w = read_wish("spicy", VOCAB, lex)
    assert w.traits == {} and w.words_unknown == ["spicy"]


# ---------------------------------------------------------------- 2. candidates

def test_choose_hidden_gems_swaps_well_known_for_little_known():
    s = merged(RULES)
    items = [mk("a"), mk("b", known="somewhat"), mk("c", known="not")]
    out = cand.choose(items, s)
    assert {i.id for i in out["movie"]} == {"a", "b"}
    out = cand.choose(items, s, hidden_gems=True)
    assert {i.id for i in out["movie"]} == {"b", "c"}


def test_phrase_counts_once_however_many_tags_it_maps_to():
    w = Wish(tags={"cozy", "silly", "witty"}, tag_sets=[{"cozy"}, {"silly", "witty"}])
    cozy_only = mk("x", tags={"cozy"})
    two_of_phrase = mk("y", tags={"silly", "witty"})
    s = merged(RULES)
    one_of_phrase = mk("z", tags={"silly"})
    assert abs(match(cozy_only, w, s, VOCAB)[0] - 0.5) < 1e-9
    assert abs(match(two_of_phrase, w, s, VOCAB)[0] - 0.5) < 1e-9
    assert abs(match(one_of_phrase, w, s, VOCAB)[0] - 0.4) < 1e-9


def test_choose_min_popularity_per_known_level():
    s = merged({"candidates": {"min_popularity": {"somewhat": 500}}})
    items = [mk("a", pop=10), mk("b", known="somewhat", pop=100),
             mk("c", known="somewhat", pop=600)]
    out = cand.choose(items, s)
    assert {i.id for i in out["movie"]} == {"a", "c"}  # well has no minimum


def test_choose_sorted_by_popularity():
    s = merged({})
    out = cand.choose([mk("a", pop=1), mk("b", pop=9), mk("c", pop=5)], s)
    assert [i.id for i in out["movie"]] == ["b", "c", "a"]


def test_choose_pool_split_by_shares():
    s = merged({"candidates": {"total": 10, "kinds": {"movie": 0.7, "tv": 0.3}}})
    out = cand.choose(make_catalog(), s)
    assert len(out["movie"]) == 7 and len(out["tv"]) == 3, {k: len(v) for k, v in out.items()}
    # the most popular come first within a kind
    assert [i.id for i in out["tv"]] == ["t0", "t1", "t2"]


def test_pool_sizes():
    assert cand.pool_sizes(["tv"], 50, {}) == {"tv": 50}
    assert cand.pool_sizes(["movie", "tv"], 10, {"movie": 0.7, "tv": 0.3}) == {"movie": 7, "tv": 3}
    assert cand.pool_sizes(["movie", "tv"], 10, {}) == {"movie": 5, "tv": 5}  # no shares: equal


def test_choose_single_kind_gets_whole_total():
    s = merged({"candidates": {"total": 8, "kinds": {"movie": 0.7, "tv": 0.3}}})
    out = cand.choose(make_catalog(), s, kinds=["tv"])
    assert list(out) == ["tv"]
    assert len(out["tv"]) == 8  # 9 well/somewhat tv items exist, total caps at 8


def test_choose_spare_room_goes_to_other_kind():
    s = merged({"candidates": {"total": 10, "kinds": {"movie": 0.5, "tv": 0.5}}})
    items = [mk(f"m{i}", pop=100 - i) for i in range(20)] + [mk(f"t{i}", kind="tv") for i in range(2)]
    out = cand.choose(items, s)
    assert len(out["tv"]) == 2
    assert len(out["movie"]) == 8, len(out["movie"])  # 5 own + 3 spare from tv


def test_choose_only_ids_keeps_everything():
    s = merged({"candidates": {"total": 3}})
    items = [mk("a", known="not", pop=0), mk("b", known="somewhat", pop=1), mk("c"), mk("d"), mk("e")]
    out = cand.choose(items, s, hidden_gems=False, only_ids=True)
    assert len(out["movie"]) == 5


def test_choose_kinds_filter():
    s = merged({})
    out = cand.choose(make_catalog(), s, kinds=["movie"])
    assert list(out) == ["movie"]


# ---------------------------------------------------------------- 3. matcher

def test_match_empty_wish_is_zero():
    item = make_catalog()[0]
    assert match(item, Wish(), merged(RULES), VOCAB) == (0.0, [])


def test_match_on_target_beats_far_away():
    s = merged(RULES)
    wish = Wish(traits={"humour": 0.9})
    near = mk("n", traits={"humour": 0.9})
    far = mk("f", traits={"humour": 0.1})
    sn, _ = match(near, wish, s, VOCAB)
    sf, _ = match(far, wish, s, VOCAB)
    assert approx(sn, 1.0) and sf == 0.0 and sn > sf


def test_match_missing_trait_is_neutral():
    s = merged(RULES)
    score, _ = match(mk("x"), Wish(traits={"humour": 0.9}), s, VOCAB)
    assert approx(score, 0.5)


def test_match_avoided_tag_lowers_score():
    s = merged(RULES)
    wish = Wish(tags={"witty"}, avoid_tags={"tense"})
    clean = mk("c", tags={"witty"})
    tense = mk("t", tags={"witty", "tense"})
    sc, _ = match(clean, wish, s, VOCAB)
    st, _ = match(tense, wish, s, VOCAB)
    assert approx(sc, 1.0) and st < sc and st == 0.0


def test_match_reasons_include_matched_tags_first():
    s = merged(RULES)
    wish = Wish(tags={"silly", "witty"}, traits={"humour": 0.9})
    item = mk("x", tags={"silly"}, traits={"humour": 0.9})
    score, reasons = match(item, wish, s, VOCAB)
    assert reasons[0] == "silly", reasons
    assert "witty" not in reasons
    assert "very funny" in reasons, reasons
    assert 0 < score < 1


# ---------------------------------------------------------------- 4. profiles

def _pop_and_described():
    items = make_catalog()
    return Population.of(items), {it.id: it for it in items}


def test_build_taste_without_reactions_scores_zero():
    pop, described = _pop_and_described()
    s = merged(RULES)
    taste = build_taste([], described, pop, s)
    assert taste.count == 0
    assert taste.score(described["m0"], pop) == (0.0, [])
    # reactions to unknown items count as none
    taste = build_taste([Reaction("nope", 1.0)], described, pop, s)
    assert taste.count == 0


def test_taste_prefers_funny_when_funny_was_loved():
    pop, described = _pop_and_described()
    s = merged(RULES)
    taste = build_taste(REACTIONS[1], described, pop, s)
    assert taste.count == 6
    funny, funny_words = taste.score(described["m12"], pop)
    grim, _ = taste.score(described["m13"], pop)
    assert funny > grim, (funny, grim)
    assert funny > 0 > grim
    assert -1.0 <= grim and funny <= 1.0
    assert "witty" in funny_words or "silly" in funny_words


def test_combine_alone_uses_other_weight():
    s = merged({"people": {"other_weight": 0.2}})
    assert approx(combine([0.5, -1.0], False, s), (0.5 + 0.2 * -1.0) / 1.2)
    s0 = merged({"people": {"other_weight": 0.0}})
    assert approx(combine([0.5, -1.0], False, s0), 0.5)
    assert combine([0.4], False, s) == 0.4
    assert combine([], False, s) == 0.0


def test_combine_together_pushes_down_disliked_item():
    s = merged({})
    split = combine([0.9, -0.8], True, s)    # one loves it, one hates it
    mild = combine([0.2, 0.2], True, s)      # both mildly like it
    assert mild > split, (mild, split)
    assert approx(split, 0.05 - 0.5 * 0.8)
    assert approx(mild, 0.2)


def test_combine_together_no_dislike_is_plain_average():
    s = merged({})
    assert approx(combine([0.6, 0.2], True, s), 0.4)


# ---------------------------------------------------------------- 5. engine

def test_recommend_picks_sorted_without_mix():
    rules = {**RULES, "mix": {}}
    result = Wishmatch(FakeAdapter(rules=rules)).recommend(Request(text="funny"))
    scores = [p.score for p in result["picks"]]
    assert scores and scores == sorted(scores, reverse=True)
    assert all(isinstance(p, Pick) for p in result["picks"])
    # funny items (humour 0.9) are on top
    assert result["picks"][0].item.traits["humour"] == 0.9


def test_recommend_excludes_unknown_items_by_default():
    result = Wishmatch(FakeAdapter()).recommend(Request(text=""))
    ids = {p.item.id for p in result["picks"]}
    assert "m19" not in ids and "t9" not in ids
    assert "m14" in ids  # "somewhat" is allowed
    assert result["candidates"] == {"movie": 17, "tv": 9}


def test_recommend_hidden_gems_include_unknown_items():
    result = Wishmatch(FakeAdapter()).recommend(Request(text="a hidden gem"))
    ids = {p.item.id for p in result["picks"]}
    assert "m19" in ids and "t9" in ids
    assert result["wish"].hidden_gems is True


def test_recommend_mixes_kinds_in_top_20():
    result = Wishmatch(FakeAdapter()).recommend(Request(text="funny"))
    top = result["picks"][:20]
    kinds = [p.item.kind for p in top]
    assert "movie" in kinds and "tv" in kinds
    assert 5 <= kinds.count("tv") <= 9, kinds.count("tv")
    # within one kind the order is still by score
    for kind in ("movie", "tv"):
        scores = [p.score for p in result["picks"] if p.item.kind == kind]
        assert scores == sorted(scores, reverse=True)


def test_recommend_kinds_filter_only_tv():
    result = Wishmatch(FakeAdapter()).recommend(Request(text="funny", kinds=["tv"]))
    assert result["picks"]
    assert {p.item.kind for p in result["picks"]} == {"tv"}
    scores = [p.score for p in result["picks"]]
    assert scores == sorted(scores, reverse=True)


def test_recommend_picks_carry_reasons():
    result = Wishmatch(FakeAdapter()).recommend(Request(text="make me laugh"))
    top = result["picks"][0]
    assert top.reasons, top
    assert set(top.reasons) & {"silly", "witty"}, top.reasons
    assert len(top.reasons) <= 3
    assert len(set(top.reasons)) == len(top.reasons)


def test_recommend_taste_changes_ranking():
    adapter = FakeAdapter(reactions=REACTIONS)
    result = Wishmatch(adapter).recommend(Request(text="", people=[1], kinds=["movie"]))
    by_id = {p.item.id: p for p in result["picks"]}
    assert by_id["m12"].score > by_id["m13"].score  # unrated funny vs unrated grim
    assert by_id["m12"].taste > 0 > by_id["m13"].taste


def test_recommend_together_request():
    adapter = FakeAdapter(reactions=REACTIONS)
    eng = Wishmatch(adapter)
    alone = eng.recommend(Request(text="", people=[1, 2], together=False, kinds=["movie"]))
    together = eng.recommend(Request(text="", people=[1, 2], together=True, kinds=["movie"]))
    a = {p.item.id: p for p in alone["picks"]}
    t = {p.item.id: p for p in together["picks"]}
    assert t and set(a) == set(t)
    # person 1 loves m12 and person 2 hates it: together it drops below the alone score
    assert t["m12"].taste < a["m12"].taste, (t["m12"].taste, a["m12"].taste)
    scores = [p.score for p in together["picks"]]
    assert scores == sorted(scores, reverse=True)


def test_recommend_only_ids_keeps_all_given():
    adapter = FakeAdapter()
    ids = ["m19", "t9", "m0"]  # two "not known" items and one well known
    result = Wishmatch(adapter).recommend(Request(text="funny", only_ids=ids))
    assert {p.item.id for p in result["picks"]} == set(ids)


def test_recommend_no_items_returns_empty():
    result = Wishmatch(FakeAdapter()).recommend(Request(text="funny", only_ids=[]))
    assert result["picks"] == [] and result["candidates"] == {}


def test_recommend_accepts_prebuilt_wish():
    eng = Wishmatch(FakeAdapter())
    wish = Wish(tags={"eerie"})
    result = eng.recommend(Request(text="ignored words"), wish=wish)
    assert result["wish"] is wish
    assert "eerie" in result["picks"][0].reasons


def test_mix_kinds_follows_shares():
    picks = [Pick(item=mk(f"m{i}", kind="movie"), score=1.0 - i * 0.01, match=0, taste=0)
             for i in range(10)]
    picks += [Pick(item=mk(f"t{i}", kind="tv"), score=0.5 - i * 0.01, match=0, taste=0)
              for i in range(10)]
    picks.sort(key=lambda p: -p.score)
    out = mix_kinds(picks, {"movie": 0.5, "tv": 0.5})
    first = [p.item.kind for p in out[:10]]
    assert first.count("tv") == 5, first
    assert len(out) == 20
    assert mix_kinds(picks, {"movie": 1.0}) == picks  # one kind with a share: unchanged
    assert mix_kinds(picks, {}) == picks


def test_negation_reaches_only_the_next_match():
    w = read_wish("not cozy funny", VOCAB, LEX)
    assert "cozy" in w.avoid_tags
    assert w.traits.get("humour", 0) > 0.5


def test_explain_reasons():
    assert explain_reasons(["a", "b"], ["b", "c", "d"], 3) == ["a", "b", "c"]
    assert explain_reasons([], ["", "x"], 3) == ["x"]
    assert explain_reasons(["a"], [], 0) == []


# ---------------------------------------------------------------- 6. settings

def test_merged_one_level_deep_keeps_other_defaults():
    s = merged({"weights": {"taste": 0.9}, "candidates": {"total": 50}})
    assert s["weights"]["taste"] == 0.9
    assert s["weights"]["match"] == DEFAULTS["weights"]["match"]
    assert s["weights"]["quality"] == DEFAULTS["weights"]["quality"]
    assert s["candidates"]["total"] == 50
    assert s["candidates"]["known"] == ["well", "somewhat"]
    assert s["lexicon"]["negators"] == DEFAULTS["lexicon"]["negators"]


def test_merged_is_shallow_beyond_one_level():
    s = merged({"candidates": {"kinds": {"movie": 1.0}}})
    assert s["candidates"]["kinds"] == {"movie": 1.0}
    s = merged({"lexicon": {"traits": {"funny": {"humour": 0.9}}}})
    assert s["lexicon"]["traits"] == {"funny": {"humour": 0.9}}
    assert "negators" in s["lexicon"]


def test_merged_does_not_mutate_defaults():
    before = repr(DEFAULTS)
    s = merged({"weights": {"taste": 0.9}})
    s["weights"]["match"] = 99
    s["candidates"]["known"].append("not")
    assert repr(DEFAULTS) == before
    assert merged(None) == DEFAULTS
    assert merged({}) == DEFAULTS


def test_merged_non_dict_value_replaces_and_unknown_key_added():
    s = merged({"mix": {"movie": 1.0}, "extra": 5})
    assert s["mix"] == {"movie": 1.0}
    assert s["extra"] == 5


# ---------------------------------------------------------------- 7. crowd

def test_crowd_scores_follow_liked_and_disliked_neighbours():
    from wishmatch.crowd import crowd_scores
    nb = {"a": {"x": 0.9, "y": 0.2}, "b": {"y": 0.8}}
    s = crowd_scores([Reaction("a", 1.0), Reaction("b", -1.0)], nb, shrink=1.0)
    assert s.score["x"] > 0.4 and s.because["x"] == "a"
    assert s.score["y"] < 0
    assert s.get("z") == (0.0, None)


def test_extra_candidates_best_first_above_floor():
    from wishmatch.crowd import CrowdScores, extra_candidates
    a = CrowdScores(score={"x": 0.9, "y": 0.2})
    b = CrowdScores(score={"z": 0.5, "x": 0.1})
    assert extra_candidates([a, b], limit=5, min_score=0.3) == ["x", "z"]
    assert extra_candidates([a, b], limit=1, min_score=0.3) == ["x"]


class CrowdAdapter(FakeAdapter):
    """m3 is a crowd favourite of fans of m0; m12 is somewhat known and misses a tiny popularity cut."""
    NB = {"m0": {"m3": 0.9, "m12": 0.9, "m19": 0.9}}

    def crowd(self, item_ids):
        return {i: self.NB[i] for i in item_ids if i in self.NB}


def test_engine_uses_crowd_and_adds_crowd_candidates():
    rules = merged(RULES)
    rules["candidates"] = {**rules["candidates"], "total": 5, "kinds": {"movie": 1.0}}
    rules["weights"] = {**rules["weights"], "crowd": 1.0}
    ad = CrowdAdapter(rules=rules, reactions={1: [Reaction("m0", 1.0)]})
    out = Wishmatch(ad).recommend(Request(people=[1], kinds=["movie"]))
    ids = [p.item.id for p in out["picks"]]
    assert "m12" in ids and out["crowd_added"] >= 1, ids        # pulled in past the popularity cut
    assert "m19" not in ids                                       # "not" known: still left out
    by = {p.item.id: p for p in out["picks"]}
    assert by["m3"].crowd > 0 and by["m3"].because == "m0"
    others = [p for p in out["picks"] if p.item.id not in ("m3", "m12")]
    assert all(by["m3"].score > p.score for p in others if p.item.traits == by["m3"].item.traits)


def test_engine_without_crowd_weight_skips_crowd():
    ad = CrowdAdapter(reactions={1: [Reaction("m0", 1.0)]})
    out = Wishmatch(ad).recommend(Request(people=[1], kinds=["movie"]))
    assert out["crowd_added"] == 0 and all(p.crowd == 0.0 for p in out["picks"])


# ---------------------------------------------------------------- 8. model wish reader

def test_parse_answer_keeps_only_vocabulary_words():
    from wishmatch.llm import parse_answer
    w = parse_answer('{"w": ["cozy", "spaceship"], "a": ["tense"], "s": {"humour": 8, "nonsense": 3}}', VOCAB)
    assert w.tags == {"cozy"} and w.avoid_tags == {"tense"} and w.traits == {"humour": 0.8}
    assert parse_answer("not json", VOCAB) is None and parse_answer(None, VOCAB) is None
    assert parse_answer('Sure! {"w": ["witty"], "a": []} hope that helps', VOCAB).tags == {"witty"}


def test_model_reading_is_laid_over_the_lookup():
    from wishmatch.llm import LLMWishReader
    calls = []

    def fake(prompt, schema=None, system=""):
        calls.append(prompt)
        return '{"w": ["cozy", "witty"], "a": ["eerie"]}'

    r = LLMWishReader(fake, avoid_groups=None)
    lookup = read_wish("relaxing, make me laugh", VOCAB, LEX)
    w = r.read("relaxing, make me laugh", VOCAB, lookup)
    assert w.source == "model+lookup"
    assert w.traits == lookup.traits                       # the lookup's dials stay
    assert {"cozy", "silly", "witty"} <= w.tags
    assert w.avoid_tags == set()                           # no "no / not" typed: the model's avoid list is dropped
    assert {"cozy"} in [ts for ts in w.tag_sets if "cozy" in ts] or any("cozy" in ts for ts in w.tag_sets)
    r.read("Relaxing,  make me LAUGH", VOCAB, lookup)
    assert len(calls) == 1                                 # same words: remembered


def test_model_avoid_counts_only_when_ruled_out_and_in_allowed_groups():
    from wishmatch.llm import LLMWishReader
    r = LLMWishReader(lambda *a, **k: '{"w": [], "a": ["eerie", "a bad day"]}', avoid_groups=["feels"])
    w = r.read("something fun but not eerie", VOCAB, read_wish("something fun but not eerie", VOCAB, LEX))
    assert w.avoid_tags == {"eerie"}, w.avoid_tags          # "a bad day" is not in the allowed groups


def test_slow_or_failing_model_falls_back_to_lookup():
    import threading
    from wishmatch.llm import LLMWishReader
    gate = threading.Event()

    def slow(*a, **k):
        gate.wait(2)
        return '{"w": ["cozy"], "a": []}'

    r = LLMWishReader(slow, wait_seconds=0.05)
    lookup = read_wish("make me laugh", VOCAB, LEX)
    first = r.read("make me laugh", VOCAB, lookup)
    assert first.source == "lookup" and "cozy" not in first.tags
    gate.set()
    for _ in range(100):
        if r.cached("make me laugh"):
            break
        threading.Event().wait(0.02)
    assert "cozy" in r.read("make me laugh", VOCAB, lookup).tags   # the late answer is used next time
    broken = LLMWishReader(lambda *a, **k: None)
    assert broken.read("make me laugh", VOCAB, lookup).source == "lookup"


def test_engine_uses_reader_and_survives_its_errors():
    class Boom:
        def read(self, *a):
            raise RuntimeError("down")

    out = Wishmatch(FakeAdapter(), reader=Boom()).recommend(Request(text="make me laugh"))
    assert out["wish"].source == "lookup" and out["picks"]


def test_word_question_falls_back_to_template():
    from wishmatch.llm import word_question
    t = word_question("tone", ["silly", "witty"])
    assert t["source"] == "template" and t["options"] == ["silly", "witty"]
    good = word_question("tone", ["silly", "witty"],
                         lambda *a, **k: '{"question": "Goofy or clever?", "options": ["Goofy", "Clever"]}')
    assert good == {"question": "Goofy or clever?", "options": ["Goofy", "Clever"], "source": "model"}
    bad = word_question("tone", ["silly", "witty"], lambda *a, **k: '{"question": "Hm?", "options": ["One"]}')
    assert bad["source"] == "template"


def test_question_worder_asks_the_model_once_per_question():
    from wishmatch.llm import QuestionWorder
    calls = []

    def model(*a, **k):
        calls.append(a[0])
        return '{"question": "Goofy or clever?", "options": ["Goofy", "Clever"]}'

    w = QuestionWorder(model, wait_seconds=1)
    first = w.word("tone", ["silly", "witty"])
    again = w.word("tone", ["silly", "witty"])
    assert first == again == {"question": "Goofy or clever?", "options": ["Goofy", "Clever"], "source": "model"}
    assert len(calls) == 1
    w.word("tone", ["witty", "silly"])                       # a different choice is worded on its own
    assert len(calls) == 2


def test_question_worder_slow_model_uses_template_now_and_wording_later():
    import threading
    from wishmatch.llm import QuestionWorder
    release = threading.Event()

    def slow(*a, **k):
        release.wait(2)
        return '{"question": "Goofy or clever?", "options": ["Goofy", "Clever"]}'

    w = QuestionWorder(slow, wait_seconds=0.05)
    assert w.word("tone", ["silly", "witty"])["source"] == "template"
    release.set()
    for _ in range(100):                                     # the background answer lands in the cache
        if w.word("tone", ["silly", "witty"])["source"] == "model":
            break
    assert w.word("tone", ["silly", "witty"])["source"] == "model"


def test_question_worder_does_not_remember_failures():
    from wishmatch.llm import QuestionWorder
    answers = [None, '{"question": "Goofy or clever?", "options": ["Goofy", "Clever"]}']
    w = QuestionWorder(lambda *a, **k: answers.pop(0), wait_seconds=1)
    assert w.word("tone", ["silly", "witty"])["source"] == "template"
    assert w.word("tone", ["silly", "witty"])["source"] == "model"


def test_ollama_client_fails_quietly_without_a_server():
    from wishmatch import OllamaClient
    c = OllamaClient("http://127.0.0.1:9", "tiny", timeout=0.5)
    assert c.generate("hi") is None and c.last_error
    assert c.status()["reachable"] is False


# ---------------------------------------------------------------- 9. questions

def _picks(items):
    from wishmatch.types import Pick
    return [Pick(item=it, score=1.0 - i * 0.01, match=0.0, taste=0.0) for i, it in enumerate(items)]


def test_question_asks_about_a_trait_the_picks_split_on():
    from wishmatch.questions import next_question
    items = [mk(f"x{i}", traits={"humour": 0.9 if i % 2 else 0.1, "energy": 0.5}) for i in range(20)]
    q = next_question(_picks(items), Wish(), VOCAB, merged(RULES), set())
    assert q and q["id"] == "trait:humour" and [o["id"] for o in q["options"]] == ["low", "high"]
    assert all(len(o["examples"]) == 2 for o in q["options"])


def test_no_question_on_what_the_wish_already_says_or_when_picks_agree():
    from wishmatch.questions import next_question
    items = [mk(f"x{i}", traits={"humour": 0.9 if i % 2 else 0.1}) for i in range(20)]
    assert next_question(_picks(items), Wish(traits={"humour": 0.9}), VOCAB, merged(RULES), set()) is None
    same = [mk(f"y{i}", traits={"humour": 0.8}, tags={"cozy"}) for i in range(20)]
    assert next_question(_picks(same), Wish(), VOCAB, merged(RULES), set()) is None


def test_tag_question_offers_tags_that_split_the_picks():
    from wishmatch.questions import next_question
    items = [mk(f"z{i}", traits={"humour": 0.7}, tags={"silly"} if i % 2 else {"witty"}) for i in range(20)]
    q = next_question(_picks(items), Wish(), VOCAB, merged(RULES), set())
    assert q and q["kind"] == "tags" and {o["id"] for o in q["options"]} == {"silly", "witty"}


def test_question_limit_and_asked_ones_are_respected():
    from wishmatch.questions import next_question
    items = [mk(f"x{i}", traits={"humour": 0.9 if i % 2 else 0.1}) for i in range(20)]
    s = merged(RULES)
    assert next_question(_picks(items), Wish(), VOCAB, s, {"trait:humour"}) is None
    assert next_question(_picks(items), Wish(), VOCAB, s, {"a", "b"}) is None      # max 2 already asked


def test_answer_narrows_the_wish_at_half_weight():
    from wishmatch.questions import apply_answer, next_question
    items = [mk(f"x{i}", traits={"humour": 0.9 if i % 2 else 0.1}) for i in range(20)]
    q = next_question(_picks(items), Wish(), VOCAB, merged(RULES), set())
    w = apply_answer(Wish(tags={"cozy"}, tag_sets=[{"cozy"}]), q, "high")
    assert w.traits == {"humour": 0.8} and w.weights["humour"] == 0.5
    assert apply_answer(w, q, "skip") is w
    try:
        apply_answer(w, q, "nope")
        raise AssertionError("an unknown option must be refused")
    except ValueError:
        pass
    # the typed phrase still counts twice as much as the answer
    funny_not_cozy = mk("a", traits={"humour": 0.8})
    cozy_not_funny = mk("b", traits={"humour": 0.1}, tags={"cozy"})
    s = merged(RULES)
    assert match(cozy_not_funny, w, s, VOCAB)[0] > match(funny_not_cozy, w, s, VOCAB)[0]


# ---------------------------------------------------------------- declared likes / dislikes

def test_declared_tags_move_taste_without_reactions():
    pop, described = _pop_and_described()
    s = merged(RULES)
    assert s["taste"]["declared_weight"] == 0.3
    taste = build_taste([], described, pop, s, {"likes": ["cozy"], "dislikes": ["tense"]})
    assert taste.count == 0                      # count stays the number of reactions
    cozy, why = taste.score(described["m0"], pop)    # cozy, witty, silly, a bad day
    tense, _ = taste.score(described["m1"], pop)     # tense, eerie
    assert cozy > 0 > tense and "cozy" in why
    assert approx(taste.tags["cozy"], 0.3) and approx(taste.tags["tense"], -0.3)
    # nothing declared and nothing reacted to still scores zero
    assert build_taste([], described, pop, s, {"likes": [], "dislikes": []}).score(described["m0"], pop) == (0.0, [])


def test_declared_tags_add_to_reaction_affinity_and_clamp():
    pop, described = _pop_and_described()
    s = merged(RULES)
    plain = build_taste(REACTIONS[1], described, pop, s)
    with_dislike = build_taste(REACTIONS[1], described, pop, s, {"dislikes": ["cozy"]})
    assert with_dislike.tags["cozy"] < plain.tags["cozy"]
    assert approx(plain.tags["cozy"] - with_dislike.tags["cozy"], 0.3) or with_dislike.tags["cozy"] == -1.0
    s2 = merged({**RULES, "taste": {"declared_weight": 5.0}})
    loud = build_taste([], described, pop, s2, {"likes": ["cozy"], "dislikes": ["tense"]})
    assert loud.tags["cozy"] == 1.0 and loud.tags["tense"] == -1.0
    off = build_taste([], described, pop, merged({**RULES, "taste": {"declared_weight": 0.0}}), {"likes": ["cozy"]})
    assert off.score(described["m0"], pop) == (0.0, [])


def test_adapter_profile_is_optional_and_changes_ranking():
    # an adapter without profile() works as before
    plain = Wishmatch(FakeAdapter()).recommend(Request(text="", people=[1], kinds=["movie"]))
    assert all(p.taste == 0 for p in plain["picks"])

    class WithProfile(FakeAdapter):
        def profile(self, person):
            return {"likes": ["cozy"], "dislikes": ["tense"]} if person == 1 else None

    result = Wishmatch(WithProfile()).recommend(Request(text="", people=[1], kinds=["movie"]))
    by_id = {p.item.id: p for p in result["picks"]}
    assert by_id["m12"].taste > 0 > by_id["m13"].taste        # unrated cozy vs unrated tense
    assert by_id["m12"].score > by_id["m13"].score
    other = Wishmatch(WithProfile()).recommend(Request(text="", people=[2], kinds=["movie"]))
    assert all(p.taste == 0 for p in other["picks"])          # person 2 declared nothing



class SeenAdapter(FakeAdapter):
    def seen_by(self, person):
        return {"m0"} if person == 1 else set()


def test_together_pushes_down_what_one_person_has_seen():
    rules = merged(RULES)
    rules["people"] = {**rules["people"], "seen_penalty": 5.0}
    out = Wishmatch(SeenAdapter(rules=rules)).recommend(Request(people=[1, 2], together=True, kinds=["movie"]))
    ids = [p.item.id for p in out["picks"]]
    assert ids[-1] == "m0", ids
    alone = Wishmatch(SeenAdapter(rules=rules)).recommend(Request(people=[1, 2], kinds=["movie"]))
    assert [p.item.id for p in alone["picks"]][-1] != "m0"     # alone: the adapter's filter's job, no penalty here

if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    tests.sort(key=lambda p: p[1].__code__.co_firstlineno)
    for name, fn in tests:
        fn()
        print("ok", name)
    print(f"{len(tests)} tests passed")
