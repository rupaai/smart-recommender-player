"""
Recommender Service (services/recommender_service.py)
Multi-stage candidate scoring: SQL hard filters and a Bayesian quality cut,
personal taste, the request's genres and words, vibe similarity, MMR
diversity and pagination. Word lists and weights: recommender_rules.yaml.
"""

from __future__ import annotations

import random
import re
import math
import time
from typing import Any, Dict, List, Optional, Tuple, Set

import requests

from services.recommender_rules import RULES, foreign_gate, passes_foreign_gate, word_index
from database import DatabaseManager


# ===========================================================================
# Helpers & Math Utilities
# ===========================================================================
def _split_lower(csv: Optional[str]) -> List[str]:
    """Splits a comma-separated string into lowercase stripped tokens."""
    if not csv:
        return []
    return [t.strip().lower() for t in str(csv).split(",") if t.strip()]


def jaccard_similarity(set_a: Set[str], set_b: Set[str]) -> float:
    """Measures set overlap between genre/keyword sets for MMR diversity."""
    if not set_a or not set_b:
        return 0.0
    intersection = len(set_a.intersection(set_b))
    union = len(set_a.union(set_b))
    return intersection / union if union > 0 else 0.0


def calculate_weighted_rating(
    rating: float,
    num_votes: int,
    min_votes: int = 1000,
    global_mean: float = 6.5,
) -> float:
    """Applies IMDb's Bayesian Weighted Rating formula to eliminate 1-vote 10/10 noise."""
    if num_votes <= 0:
        return global_mean
    return (num_votes / (num_votes + min_votes)) * rating + (
        min_votes / (num_votes + min_votes)
    ) * global_mean


# ===========================================================================
# Rules (recommender_rules.yaml)
# ===========================================================================
# Every word list, mood card, place name and tuning number lives in
# recommender_rules.yaml (services/recommender_rules.py loads it). The names
# below are views of that file so the rest of the code reads like before.
_W = RULES["weights"]
_CONCEPT = RULES["concept_request"]

MOOD_MAPPINGS: Dict[int, Dict[str, Any]] = {
    int(k): {"name": str(v.get("name", "")), "vibe": str(v.get("vibe", "")),
             "genres": list(v.get("genres") or [])}
    for k, v in (RULES["moods"] or {}).items()
}
MOOD_MAPPINGS.setdefault(8, {"name": "Surprise Me!", "vibe": "anything goes", "genres": []})

# Places and languages ("south asian", "korean", "in hindi"). Language is the
# reliable signal (TMDB mislabels co-production countries), so both must match.
ORIGIN_INTENT: Dict[str, Dict[str, Any]] = {str(k).lower(): v for k, v in (RULES["origins"] or {}).items()}

# word -> [genres]. Names are hard filters ("comedy"); hints are a strong pull ("laugh").
GENRE_NAMES: Dict[str, List[str]] = word_index(RULES["genre_names"])
GENRE_HINTS: Dict[str, List[str]] = word_index(RULES["genre_hints"])
TONES: Dict[str, Dict[str, Any]] = {str(k).lower(): v or {} for k, v in (RULES["tones"] or {}).items()}

THEMES: Dict[str, Dict[str, Any]] = {
    name: {"label": spec.get("label", name), "terms": [str(t).lower() for t in spec.get("terms") or []]}
    for name, spec in (_CONCEPT.get("themes") or {}).items()
}
CONCEPT_REQUEST_TERMS = frozenset(str(w).lower() for w in _CONCEPT.get("words") or [])
# Compared with a film's lower-cased genres.
CONCEPT_INCOMPATIBLE_GENRES = frozenset(str(g).lower() for g in _CONCEPT.get("penalty_genres") or [])

GUILTY_PLEASURE_KEYWORDS = frozenset(str(w).lower() for w in RULES["guilty_pleasure"] or [])
ANY_LANGUAGE_WORDS = frozenset(str(w).lower() for w in (RULES["foreign_titles"] or {}).get("any_language_words") or [])
TV_GENRE_ALIASES: Dict[str, List[str]] = {str(k).lower(): list(v or []) for k, v in (RULES["tv_genre_aliases"] or {}).items()}

# Words too generic to carry signal — dropped before any matching so they
# don't produce spurious overview hits. (Grammar, not taste: kept in code.)
QUERY_STOPWORDS = frozenset({
    "would", "like", "want", "wantto", "movie", "film", "watch", "movies", "films",
    "something", "high", "quality", "good", "great", "best", "really",
    "should", "have", "that", "this", "with", "but", "not", "too", "very",
    "some", "kind", "sort", "bit", "which", "there", "here",
    "about", "more", "less", "also", "then", "than", "them", "they", "been",
    "were", "was", "you", "your", "our", "we", "us", "me", "my", "and", "for",
    "from", "one", "two", "all", "can", "will", "just", "get", "got", "see",
    "tonight", "show", "shows", "series", "make", "makes", "please", "anything",
})

# Tokens that must never be treated as a content word on their own.
QUERY_NON_GENRE = frozenset({"scifi", "concept", "concepts", "idea", "ideas"})

# Words a negation reaches through when it lists genres: "no animation or
# musicals", "nothing animated, scary or kids stuff".
GENRE_LIST_FILLERS = {"or", "and", "nor", "any", "more", "movies", "movie", "films",
                      "film", "stuff", "please", "fi", "too", "much", "a", "an", "really"}
GENRE_NEGATION_REACH = 6


def _is_genre_word(word: str) -> bool:
    return word in GENRE_NAMES or word in GENRE_HINTS


def _genre_negated(tokens: List[str], i: int) -> bool:
    """A negator before a genre word, reaching back through a list of genres."""
    for j in range(i - 1, max(i - 1 - GENRE_NEGATION_REACH, -1), -1):
        # "less animation" is a softer wish than "no animation": not an exclusion.
        if tokens[j] in NEGATORS and tokens[j] != "less":
            return True
        if _is_genre_word(tokens[j]) or tokens[j] in GENRE_LIST_FILLERS or tokens[j] in TONES:
            continue
        return False
    return False


def _genre_lessened(tokens: List[str], i: int) -> bool:
    """"less animation", "less scary stuff": a soft push down, not an exclusion."""
    for j in range(i - 1, max(i - 1 - GENRE_NEGATION_REACH, -1), -1):
        if tokens[j] == "less":
            return True
        if _is_genre_word(tokens[j]) or tokens[j] in GENRE_LIST_FILLERS:
            continue
        return False
    return False


# ===========================================================================
# Recommender Service Engine
# ===========================================================================
PROFILE_MIX: Dict[str, Tuple[float, float]] = {
    # (own weight, other weight), where "own" ALWAYS means the viewer the
    # result is for, never profile 1. `me` and `partner` share weights: the id
    # swap in rank_candidates_profile_aware re-points "own" at the right person.
    str(k): (float(v[0]), float(v[1])) for k, v in (RULES["profile_mix"] or {}).items()
}
PROFILE_MIX.setdefault("together", (0.6, 0.4))
PROFILE_MIX.setdefault("me", (0.75, 0.25))
PROFILE_MIX.setdefault("partner", PROFILE_MIX["me"])

# Memory of what Discover already showed (see RecommenderService._apply_memory).
# A title shown and not opened loses MEMORY_IGNORED points per showing, fading to
# nothing over MEMORY_IGNORED_DAYS; "Not this one" removes it for MEMORY_DISMISSED_DAYS.
_MEM = RULES["memory"]
MEMORY_IGNORED = float(_MEM["ignored"])
MEMORY_IGNORED_CAP = float(_MEM["ignored_cap"])
MEMORY_IGNORED_DAYS = float(_MEM["ignored_days"])
MEMORY_DISMISSED = float(_MEM["dismissed"])
MEMORY_DISMISSED_DAYS = int(_MEM["dismissed_days"])

# Averaged taste (recommender.taste_formula: average). A genre's affinity is how
# much more (or less) you liked its films than your own average, shrunk towards 0
# for genres with few ratings; a film gets the MEAN over its genres, so neither
# rating lots of one genre nor carrying many genres inflates it.
_TASTE = RULES["taste"]
TASTE_GENRE_SHRINK = float(_TASTE["genre_shrink"])
TASTE_MEAN_SHRINK = float(_TASTE["mean_shrink"])
TASTE_KEYWORD_SHRINK = float(_TASTE["keyword_shrink"])
TASTE_KEYWORD_MIN = int(_TASTE["keyword_min"])        # a keyword needs this many rated films to count
TASTE_GENRE_SCALE = float(_TASTE["genre_scale"])
TASTE_KEYWORD_SCALE = float(_TASTE["keyword_scale"])
TASTE_KEYWORD_FULL = int(_TASTE["keyword_full"])      # keyword evidence counts fully from this many matches


def build_taste_affinity(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """rows: [{genres, tier2_keywords, score -1..+1}] for one profile ->
    {"genres": {g: affinity}, "keywords": {k: affinity}, "n": count}."""
    if not rows:
        return {"genres": {}, "keywords": {}, "n": 0}
    # Your average, shrunk towards neutral while there are few ratings: with one
    # rating, "above your own average" would be nothing at all.
    mean = sum(r["score"] for r in rows) / (len(rows) + TASTE_MEAN_SHRINK)

    def affinities(field: str, shrink: float, min_count: int) -> Dict[str, float]:
        total: Dict[str, float] = {}
        count: Dict[str, int] = {}
        for r in rows:
            for key in set(_split_lower(r.get(field))):
                total[key] = total.get(key, 0.0) + (r["score"] - mean)
                count[key] = count.get(key, 0) + 1
        return {k: total[k] / (count[k] + shrink) for k in total if count[k] >= min_count}

    return {
        "genres": affinities("genres", TASTE_GENRE_SHRINK, 1),
        "keywords": affinities("tier2_keywords", TASTE_KEYWORD_SHRINK, TASTE_KEYWORD_MIN),
        "n": len(rows),
    }


def averaged_taste(item_genres: Set[str], item_keywords: Set[str], affinity: Dict[str, Any]) -> float:
    """One film's taste score under the averaged formula (can be negative)."""
    genres = affinity.get("genres") or {}
    keywords = affinity.get("keywords") or {}
    score = 0.0
    if item_genres:
        score += TASTE_GENRE_SCALE * sum(genres.get(g, 0.0) for g in item_genres) / len(item_genres)
    hits = [keywords[k] for k in item_keywords if k in keywords]
    if hits:
        score += (TASTE_KEYWORD_SCALE * sum(hits) / len(hits)
                  * min(1.0, len(hits) / TASTE_KEYWORD_FULL))
    return score


# Genre spread: "shelves" a page should not fill up with unless asked. At most
# SPREAD_MAX titles from one shelf in any SPREAD_WINDOW consecutive picks.
SPREAD_GENRES = frozenset(str(g).lower() for g in RULES["spread"]["shelves"] or [])
SPREAD_MAX = int(RULES["spread"]["max"])
SPREAD_WINDOW = int(RULES["spread"]["window"])


def apply_genre_spread(items: List[Dict[str, Any]], exempt: Optional[Set[str]] = None,
                       max_per_window: int = SPREAD_MAX, window: int = SPREAD_WINDOW) -> List[Dict[str, Any]]:
    """Reorder so no shelf genre takes more than `max_per_window` of any `window`
    consecutive picks. Titles are only moved later, never dropped; when nothing
    fits, the best remaining title goes next anyway. `exempt` holds the genres
    the request asked for (lower case), which are never held back."""
    shelves = SPREAD_GENRES - {g.lower() for g in (exempt or set())}
    if not shelves:
        return items
    tagged = [(it, set(_split_lower(it.get("genres"))) & shelves) for it in items]
    out: List[Tuple[Dict[str, Any], Set[str]]] = []
    while tagged:
        recent: Dict[str, int] = {}
        for _, gs in out[-(window - 1):]:
            for g in gs:
                recent[g] = recent.get(g, 0) + 1
        pick = next((k for k, (_, gs) in enumerate(tagged)
                     if all(recent.get(g, 0) < max_per_window for g in gs)), 0)
        out.append(tagged.pop(pick))
    return [it for it, _ in out]


VIBE_WEIGHT = float(_W["vibe"])
VIBE_SHORTLIST = int(_W["vibe_shortlist"])

# A: you both liked it — the scarcest and most useful signal
# B: one of you loved it, the other hasn't seen it — "he'd recommend this"
# C: exploration
TIER_BOOSTS: Dict[str, float] = {str(k): float(v) for k, v in (RULES["tier_boosts"] or {}).items()}
for _tier in ("A", "B", "C"):
    TIER_BOOSTS.setdefault(_tier, 0.0)


def _origin_negated(before: str) -> bool:
    """True when the words just before an origin phrase negate it."""
    words = re.findall(r"[a-z]+", before)[-3:]
    if not words:
        return False
    return (any(w in NEGATORS for w in words) or words[-1] == "except"
            or words[-2:] in (["anything", "but"], ["everything", "but"]))


def parse_origin_intent(query: Optional[str]) -> Optional[Dict[str, Any]]:
    """Detect a request for cinema of a PLACE rather than a genre.

    "South Asian movies" names an origin, not a genre, so the genre filters
    could never match it and the recommender returned Hollywood regardless.
    Matches the longest phrase first ("south asian" before "asian") so the more
    specific region wins.

    Returns countries + languages to filter on, or None if the query names no
    origin.
    """
    if not query:
        return None
    text = query.lower()
    best = None
    for phrase, spec in ORIGIN_INTENT.items():
        # Whole words only (an optional plural "s"): a bare substring test read
        # "indiana jones" as Indian, "polished" as Polish and "animated" as anime.
        m = re.search(rf"\b{re.escape(phrase)}s?\b", text)
        # "no anime", "anything but korean": a negated place is not a request
        # for it (it used to restrict the results to exactly that place).
        if m and _origin_negated(text[:m.start()]):
            continue
        if m:
            # Prefer the longest matching phrase (most specific region).
            if best is None or len(phrase) > len(best[0]):
                best = (phrase, spec)
    if best is None:
        return None
    phrase, spec = best
    countries = list(spec.get("countries", []))
    # "South Asian" covers several countries, so allow the extras too.
    for extra in spec.get("also_countries", []):
        if extra not in countries:
            countries.append(extra)
    return {
        "phrase": phrase,
        "label": spec.get("label", phrase.title()),
        "countries": countries,
        "languages": list(spec.get("languages", [])),
    }


def _theme_mentioned(theme: str, tokens: List[str]) -> bool:
    """True when the request itself names a theme (not just its genre)."""
    haystack = " ".join(tokens)
    return any(t in haystack for t in THEMES[theme]["terms"])


def _item_themes(item: Dict[str, Any]) -> List[str]:
    text = f"{item.get('title')} {item.get('overview')}".lower()
    return [name for name, spec in THEMES.items() if any(t in text for t in spec["terms"])]


def _concept_adjustment(item: Dict[str, Any], item_genres: Set[str],
                        requested_themes: Optional[List[str]] = None) -> float:
    """Concept requests: premise themes in the synopsis lift a film (more when the
    request describes that premise itself), and genres that can't carry "an idea
    to interpret" (a family cartoon) push it down. Soft: a film can still surprise us."""
    boost = 0.0
    matched = _item_themes(item)
    if matched:
        boost += min(float(_CONCEPT["theme_boost"]) + float(_CONCEPT["theme_boost_more"]) * (len(matched) - 1),
                     float(_CONCEPT["theme_boost_max"]))
        boost += float(_CONCEPT.get("named_theme_boost", 0.0)) * len(set(matched) & set(requested_themes or []))
        item["matched_themes"] = [THEMES[n]["label"] for n in matched]
    if item_genres & CONCEPT_INCOMPATIBLE_GENRES:
        boost -= float(_CONCEPT["penalty"])
    return boost


def genre_boost(item_genres: Set[str], boosts: Optional[Dict[str, float]]) -> float:
    """Points for the genres a request asked for, hinted at or leans to (negative
    for "less X"). `boosts` keys are lower case; TV's combined genres match too."""
    if not boosts or not item_genres:
        return 0.0
    have = set(item_genres)
    for g in item_genres:
        for part in g.split("&"):
            have.add(part.strip())
    return sum(w for g, w in boosts.items() if g in have)


def _word_match(item: Dict[str, Any], words: Set[str]) -> float:
    """Points for request words found in the title, synopsis or keywords."""
    if not words:
        return 0.0
    text = f"{item.get('title')} {item.get('overview')} {item.get('tier2_keywords')}".lower()
    matches = sum(1 for w in words if len(w) > 3 and w in text)
    return min(matches * float(_W["word_match"]), float(_W["word_match_max"]))


NEGATORS = {"not", "no", "never", "without", "less", "nothing", "avoid", "skip",
            "dont", "don", "isn", "aren", "nor", "none"}
# Words that end a negation's reach: "not scary but funny" negates scary only.
NEGATION_BREAKERS = {"but", "and", "or", "yet", "though", "although", "however", "just", "also"}
NEGATION_WINDOW = 3


def _negator_before(tokens: List[str], i: int) -> Optional[str]:
    """The negator up to three words back, with no "but"/"and" in between.

    A one-word window missed "not too complicated" and "no really scary".
    """
    for j in range(i - 1, max(i - 1 - NEGATION_WINDOW, -1), -1):
        if tokens[j] in NEGATION_BREAKERS:
            return None
        if tokens[j] in NEGATORS:
            return tokens[j]
    return None


def _is_negated(tokens: List[str], i: int) -> bool:
    return _negator_before(tokens, i) is not None


def parse_query_intent(query: Optional[str]) -> Dict[str, Any]:
    """Turn a free-text request into genre signals, using the word lists in
    recommender_rules.yaml.

    Returns:
        include_genres:       genres the request NAMED ("comedy"): a hard filter
        hinted_genres:        genres it described ("make me laugh"): a strong pull
        exclude_genres:       hard exclusions (named or hinted with "no", plus the
                              tones' exclusions that nothing else asks for)
        named_exclude_genres: the "no X" part of exclude_genres
        boost_weights:        {genre: points} for scoring (negative for "less X")
        boost_genres:         the genres with a positive pull
        weight:               > 0 when the request carried any recognised intent
    """
    result: Dict[str, Any] = {
        "include_genres": [], "hinted_genres": [], "exclude_genres": [],
        "named_exclude_genres": [], "boost_genres": [], "boost_weights": {},
        "weight": 0.0, "concept_requested": False, "themes": [],
        "matched_terms": [], "content_words": [], "any_language": False,
    }
    if not query or not query.strip():
        return result

    tokens = [w for w in re.findall(r"[a-z]+", query.lower()) if w]
    concept = any(w in CONCEPT_REQUEST_TERMS for w in tokens)
    named: Set[str] = set()
    hinted: Set[str] = set()
    no_genres: Set[str] = set()
    less: Set[str] = set()
    tone_boost: Set[str] = set()
    tone_exclude: Set[str] = set()
    negated_words: Set[str] = set()
    matched: List[str] = []

    for i, word in enumerate(tokens):
        genres = GENRE_NAMES.get(word) or GENRE_HINTS.get(word)
        if genres:
            negator = _negator_before(tokens, i)
            if _genre_negated(tokens, i) or (negator and negator != "less"):
                no_genres.update(genres)
                negated_words.add(word)
                matched.append(f"no {word}")
            elif _genre_lessened(tokens, i):
                less.update(genres)
                negated_words.add(word)
                matched.append(f"less {word}")
            else:
                (named if word in GENRE_NAMES else hinted).update(genres)
                matched.append(word)
            continue
        tone = TONES.get(word)
        if tone:
            matched.append(word)
            if _is_negated(tokens, i) and not tone.get("same_when_negated"):
                negated_words.add(word)        # "not relaxing" drops the tone
                continue
            tone_boost.update(tone.get("boost") or [])
            tone_exclude.update(tone.get("exclude") or [])
            continue
        if _is_negated(tokens, i):
            negated_words.add(word)            # "no zombies" must never boost zombies

    named -= no_genres
    hinted -= no_genres | named
    asked = named | hinted
    # A tone's exclusions never remove a genre the request asks for ("relaxing horror").
    exclude = no_genres | (tone_exclude - asked)

    # In a concept request, "it was chill and funny" describes the film it wants.
    scale = float(_CONCEPT["hint_weight"]) if concept else 1.0
    hint_points = float(_W["hinted_genre"]) * scale
    weights: Dict[str, float] = {}
    for g in tone_boost - exclude:
        weights[g] = float(_W["tone_genre"]) * scale
    for g in hinted:
        weights[g] = hint_points
    for g in named:
        weights[g] = float(_W["named_genre"])
    for g in less - asked:
        weights[g] = -float(_W.get("less_genre", 1.0))

    if concept:
        matched.append("concept")
    skip = set(GENRE_NAMES) | set(GENRE_HINTS) | set(TONES) | QUERY_STOPWORDS | QUERY_NON_GENRE | negated_words
    result.update({
        "include_genres": sorted(named),
        "hinted_genres": sorted(hinted),
        "exclude_genres": sorted(exclude),
        "named_exclude_genres": sorted(no_genres),
        "boost_weights": weights,
        "boost_genres": sorted(g for g, w in weights.items() if w > 0),
        "weight": round(min(1.0, 0.25 * len(matched)), 2),
        "matched_terms": matched,
        "concept_requested": concept,
        "themes": [name for name in THEMES if _theme_mentioned(name, tokens)],
        "content_words": sorted(w for w in set(tokens) if len(w) > 3 and w not in skip),
        "any_language": any(w in ANY_LANGUAGE_WORDS for w in tokens),
    })
    return result


def resolve_effective_genres(
    mood_genres: Optional[List[str]],
    chip_genres: Optional[List[str]],
    query: Optional[str],
) -> Dict[str, Any]:
    """Combine the mood card, the genre chips and the typed request into one spec.

    What you type or tap beats the mood card: genre chips and genres named in
    the request are the filter. The mood's genres are the filter only when the
    request asks for no genre of its own (and no concept); otherwise they are a
    soft pull. Typed "no X" always wins.
    """
    intent = parse_query_intent(query)
    excluded = set(intent["exclude_genres"])
    mood_list = [g for g in (mood_genres or []) if g]

    include: List[str] = []
    for g in list(chip_genres or []) + intent["include_genres"]:
        if g and g not in include and g not in excluded:
            include.append(g)

    boosts = dict(intent["boost_weights"])
    asks_genre = bool(include or intent["hinted_genres"] or intent["concept_requested"])
    if not asks_genre:
        include = [g for g in mood_list if g not in excluded]
    else:
        for g in mood_list:
            if g not in excluded and g not in boosts:
                boosts[g] = float(_W["tone_genre"])

    # The mood's own genres are never vetoed by a tone ("scary" mood + "relaxing").
    named = set(intent["named_exclude_genres"])
    exclude = [g for g in intent["exclude_genres"] if g in named or g not in mood_list]

    return {
        "include": include,
        "exclude": exclude,
        "hinted": intent["hinted_genres"],
        "boost": {g.strip().lower(): w for g, w in boosts.items() if g and g.strip()},
        "weight": intent["weight"],
        "content_words": intent.get("content_words", []),
        "matched_terms": intent.get("matched_terms", []),
        "concept_requested": intent.get("concept_requested", False),
        "themes": intent.get("themes", []),
        "any_language": intent.get("any_language", False),
    }



# ===========================================================================
# Series order
# ===========================================================================
# Fields describing WHY an item ranked where it did. When a sequel is swapped
# for the earlier film you should watch first, the earlier film inherits the
# sequel's slot (and these scores) so the list order is unchanged.
_SERIES_INHERITED_KEYS = (
    "recommendation_score", "base_weighted_rating", "taste_bonus",
    "taste_bonus_own", "taste_bonus_other", "relevance_boost", "query_boost",
    "co_view_tier", "tier_boost", "matched_themes", "vibe_similarity", "vibe_boost",
    "facet_fit", "cluster_affinity", "taste_reasons",
)
_SERIES_INTERNAL_KEYS = ("seen", "series_position")


def apply_series_order(items: List[Dict[str, Any]], db: Any) -> List[Dict[str, Any]]:
    """Make films in a series come out in watch order.

    For each film that belongs to a series (TMDB collection, or a sequel
    title like "Toy Story 3" before the collection backfill has run):
      - if an EARLIER entry hasn't been seen by either of you, it replaces the
        sequel in the same slot ("start with Philosopher's Stone, not
        Deathly Hallows Part 1");
      - only one entry per series is kept (the next one to watch);
      - otherwise the film is kept and labelled with its position.

    Order-preserving and engine-agnostic: both engines call it on their ranked
    list before pagination. Any lookup failure returns the list unchanged.
    """
    if not items or db is None or not hasattr(db, "get_series_context"):
        return items
    movie_ids = [
        int(it["id"]) for it in items
        if it.get("id") and (it.get("media_type") or "movie") == "movie"
    ]
    try:
        ctx = db.get_series_context(movie_ids)
    except Exception as e:  # never let series logic break recommendations
        print(f"[Recommender] series lookup skipped ({e})", flush=True)
        return items
    if not ctx:
        return items

    out: List[Dict[str, Any]] = []
    used_series: Set[str] = set()
    used_ids: Set[int] = set()
    for it in items:
        iid = it.get("id")
        s = ctx.get(int(iid)) if iid else None
        if not s:
            if iid not in used_ids:
                out.append(it)
                used_ids.add(iid)
            continue
        if s["key"] in used_series:
            continue  # one film per series per list
        used_series.add(s["key"])
        name = s.get("name") or "the series"
        next_up = next((m for m in s["members"] if not m["seen"]), None)

        if next_up is None or next_up["id"] == iid or next_up["series_position"] > s["position"]:
            if iid in used_ids:
                continue
            it["series_name"] = name
            it["series_position"] = s["position"]
            it["series_size"] = s["size"]
            if s["position"] > 1:
                it["series_note"] = f"#{s['position']} of {s['size']} in {name} — you've seen the earlier ones"
            out.append(it)
            used_ids.add(iid)
            continue

        if next_up["id"] in used_ids:
            continue
        rep = {k: v for k, v in next_up.items() if k not in _SERIES_INTERNAL_KEYS}
        for k in _SERIES_INHERITED_KEYS:
            if k in it:
                rep[k] = it[k]
        rep["series_name"] = name
        rep["series_position"] = next_up["series_position"]
        rep["series_size"] = s["size"]
        rep["series_note"] = (
            f"Watch first: #{next_up['series_position']} of {s['size']} in {name}"
            f" (instead of {it.get('title')})"
        )
        rep["replaced_sequel_id"] = iid
        rep["replaced_sequel_title"] = it.get("title")
        out.append(rep)
        used_ids.add(next_up["id"])
    return out


class RecommenderService:
    def __init__(
        self,
        db_manager: DatabaseManager,
        api_key: Optional[str] = None,
        enable_tier2: bool = True,
        shortlist_size: int = 500,
        final_pool: int = 200,
        vibe_index: Optional[Any] = None,
        taste_runtime: Optional[Any] = None,
        taste_formula: str = "average",
    ):
        self.db = db_manager
        self.taste_formula = taste_formula     # "average" (build_taste_affinity) or "sum" (old, capped)
        self.taste_runtime = taste_runtime     # services.taste_runtime.TasteRuntime, optional
        self.vibe_index = vibe_index           # services.vibe_index.VibeIndex, optional
        self._title_search = None
        self.api_key = api_key
        self.enable_tier2 = enable_tier2
        self.shortlist_size = shortlist_size   # Funnel 2: quality-cut pool size
        self.final_pool = final_pool           # Funnel 4: diverse pool for pagination
        self.http = requests.Session()

    # -- Vibe search (design doc §2) ----------------------------------------
    def vibe_ready(self, query: Optional[str]) -> bool:
        """True when a typed request can be answered by vibe similarity."""
        return bool(query and query.strip() and self.vibe_index is not None and self.vibe_index.is_ready())

    def _vibe_query(self, query: str) -> Optional[Dict[str, Any]]:
        from services.vibe_index import resolve_like_titles

        if self._title_search is None:
            from services.catalogue_search import CatalogueSearch
            self._title_search = CatalogueSearch(self.db)
        try:
            like = resolve_like_titles(query, self.db, self._title_search)
            vec = self.vibe_index.query_vector(query, [t["id"] for t in like])
        except Exception as e:
            print(f"[Recommender] Vibe query failed ({e}); using the older ranking.", flush=True)
            return None
        if vec is None:
            return None
        return {"vector": vec, "like": like}

    def _apply_vibe(self, ranked: List[Dict[str, Any]], vibe: Dict[str, Any]) -> List[Dict[str, Any]]:
        """score += VIBE_WEIGHT · similarity, rescaled over this candidate pool.

        Raw cosines from bge sit in a narrow band (~0.55–0.8), so they are
        stretched from the pool's 25th percentile (0) to its best match (1).
        Titles added after the last index build have no vector and get 0.
        """
        sims = self.vibe_index.similarities(vibe["vector"], [it["id"] for it in ranked if it.get("id")])
        if not sims:
            return ranked
        values = sorted(sims.values())
        lo, hi = values[len(values) // 4], values[-1]
        span = max(hi - lo, 1e-6)
        like_ids = {t["id"] for t in vibe["like"]}
        for it in ranked:
            s = sims.get(it.get("id"))
            if s is None:
                continue
            boost = VIBE_WEIGHT * min(max((s - lo) / span, 0.0), 1.0)
            it["vibe_similarity"] = round(s, 3)
            it["vibe_boost"] = round(boost, 2)
            it["recommendation_score"] = round(float(it.get("recommendation_score") or 0.0) + boost, 2)
            if like_ids and it["id"] in like_ids:
                it["vibe_named"] = True
        ranked = [it for it in ranked if it.get("id") not in like_ids]   # don't suggest the film you named
        ranked.sort(key=lambda it: it.get("recommendation_score") or 0.0, reverse=True)
        return ranked

    def _apply_memory(self, ranked: List[Dict[str, Any]], skip_session_id: Optional[int]) -> List[Dict[str, Any]]:
        """Push down what Discover showed lately and nobody opened, and keep
        "Not this one" titles out for a month. Without this every search
        returned the same top 50, because the scores never change between nights."""
        try:
            memory = self.db.get_shown_memory(MEMORY_DISMISSED_DAYS, skip_session_id)
        except Exception as e:  # noqa: BLE001
            print(f"[Recommender] Shown-memory lookup failed ({e}); ranking without it.", flush=True)
            return ranked
        if not memory:
            return ranked
        penalties: Dict[int, float] = {}
        for iid, m in memory.items():
            penalty = sum(MEMORY_IGNORED * (1.0 - age / MEMORY_IGNORED_DAYS)
                          for age in m["ignored_ages"] if age < MEMORY_IGNORED_DAYS)
            penalty = min(penalty, MEMORY_IGNORED_CAP)
            if m["dismissed"]:
                penalty += MEMORY_DISMISSED
            if penalty > 0:
                penalties[iid] = penalty
        # A whole series shares the penalty: series order swaps a sequel for the
        # first film you haven't seen, which would bring that film straight back.
        try:
            ctx = self.db.get_series_context(list(penalties)) if penalties else {}
        except Exception:  # noqa: BLE001
            ctx = {}
        for iid, s in ctx.items():
            for member in s.get("members") or []:
                mid = member.get("id")
                if mid is not None and penalties.get(mid, 0.0) < penalties.get(iid, 0.0):
                    penalties[mid] = penalties[iid]
        for it in ranked:
            penalty = penalties.get(it.get("id"), 0.0)
            if penalty > 0:
                it["memory_penalty"] = round(penalty, 2)
                it["recommendation_score"] = round(float(it.get("recommendation_score") or 0.0) - penalty, 2)
        ranked.sort(key=lambda it: it.get("recommendation_score") or 0.0, reverse=True)
        return ranked

    def _resolve_profiles(self, profile: str) -> Tuple[int, int]:
        """Maps a profile selector to concrete profile ids.

        `together` is NOT a stored profile — it is a computed blend over both,
        so storing it would double-count every film both people rated.
        """
        ids = sorted(self.db.get_all_profile_tastes().keys())
        if not ids:
            return (1, 2)
        primary = ids[0]
        other = ids[1] if len(ids) > 1 else primary
        return (primary, other)

    def _taste_bonus(
        self,
        item_genres: Set[str],
        item_keywords: Set[str],
        own_taste: Dict[str, Any],
        other_taste: Dict[str, Any],
        mix: Tuple[float, float],
    ) -> Tuple[float, float, float]:
        """Blended taste bonus for one item, plus its own/other components.

        Profiles are blended HERE, at scoring time, never pre-averaged into a
        single profile — that would turn "you like horror, he doesn't" into a
        lukewarm average nobody actually wants.
        """
        own_w, other_w = mix

        def bonus_for(taste: Dict[str, Any]) -> float:
            if "affinity" in taste:
                return averaged_taste(item_genres, item_keywords, taste["affinity"])
            genres = taste.get("preferred_genres", {})
            keywords = taste.get("preferred_keywords", {})
            total = 0.0
            for g in item_genres:
                if g in genres:
                    total += genres[g] * 0.2
            for k in item_keywords:
                if k in keywords:
                    total += keywords[k] * 0.15
            return min(total, 2.0)

        own_bonus = bonus_for(own_taste)
        other_bonus = bonus_for(other_taste)
        blended = own_w * own_bonus + other_w * other_bonus
        return blended, own_bonus, other_bonus

    def rank_candidates_profile_aware(
        self,
        candidates: List[Dict[str, Any]],
        profile: str = "together",
        query: Optional[str] = None,
        exclude_ids: Optional[Set[int]] = None,
        content_words: Optional[List[str]] = None,
        genre_boosts: Optional[Dict[str, float]] = None,
        concept_requested: bool = False,
        requested_themes: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Profile-aware scoring: Bayesian quality + blended taste + co-view tier.

        The tier boost is what makes "together" work. Without it a Tier A film
        (you both loved it) always loses to a marginally better-rated unseen
        one, and your shared favourites quietly disappear from the list.
        """
        tastes = self.db.get_all_profile_tastes()
        if not tastes:
            tastes = {1: {"preferred_genres": {}, "preferred_keywords": {},
                          "rating_count": 0, "name": "Me", "weight": 0.0},
                      2: {"preferred_genres": {}, "preferred_keywords": {},
                          "rating_count": 0, "name": "Partner", "weight": 0.0}}
        ids = sorted(tastes.keys())
        own_id, other_id = ids[0], (ids[1] if len(ids) > 1 else ids[0])

        if profile == "partner":
            own_id, other_id = other_id, own_id   # view from his seat

        own_taste = tastes.get(own_id, {})
        other_taste = tastes.get(other_id, {})
        if self.taste_formula == "average":
            own_taste = {**own_taste, "affinity": build_taste_affinity(self.db.get_profile_taste_rows(own_id))}
            other_taste = {**other_taste, "affinity": build_taste_affinity(self.db.get_profile_taste_rows(other_id))}

        # For `me`/`partner` the mix is defined from the VIEWER's seat, so
        # swapping the ids must also swap which weight is "own". Swapping ids
        # alone would leave both modes using the same 0.75 on profile 1, making
        # `me` and `partner` return identical lists.
        if profile == "me":
            own_w, other_w = PROFILE_MIX["me"]
        elif profile == "partner":
            own_w, other_w = PROFILE_MIX["partner"]
        else:
            own_w, other_w = PROFILE_MIX["together"]
        mix = (own_w, other_w)

        coview = self.db.get_item_co_view(
            [c["id"] for c in candidates if c.get("id")], viewer_profile_id=own_id
        )
        exclude = exclude_ids or set()
        # In `me`/`partner` mode the viewer has no use for films they've already
        # rated. In `together` mode both may still have untested picks (tier B),
        # so only films BOTH have seen are dropped — handled upstream.
        viewer_seen_ids = {
            iid for iid, cv in coview.items() if own_id in (cv.get("seen_by") or [])
        }
        if profile in ("me", "partner"):
            exclude = exclude | viewer_seen_ids
        query_words = set(content_words) if content_words else set()
        # `genre_boosts` come from the request spec (resolve_effective_genres):
        # points for genres it named, hinted at ("laugh" -> Comedy) or leans to.

        # Bayesian prior must scale with the pool being ranked. A fixed
        # min_votes=1000 suits a mainstream catalogue averaging ~820 votes, but
        # Bangladeshi cinema averages 3.7 and peaks at 190 — every title was
        # pinned to the 6.5 prior, so ranking degenerated to noise and 1-vote
        # films tied classics. Derive the prior from the actual candidates:
        # strong-attested regions are unaffected, small cinemas get a prior
        # they can actually climb out of.
        pool_votes = [int(c.get("vote_count") or 0) for c in candidates]
        pool_votes.sort()
        median_votes = pool_votes[len(pool_votes) // 2] if pool_votes else 0
        prior_min_votes = max(10, min(1000, median_votes))

        scored: List[Tuple[float, Dict[str, Any]]] = []
        for item in candidates:
            if item.get("id") in exclude:
                continue

            base_score = calculate_weighted_rating(
                float(item.get("rating") or 6.0),
                int(item.get("vote_count") or 100),
                min_votes=prior_min_votes,
            )

            item_genres = set(_split_lower(item.get("genres")))
            item_keywords = set(_split_lower(item.get("tier2_keywords")))
            # Genres the request asked for, independent of either person's taste,
            # plus request words found in the title, synopsis or keywords.
            relevance_boost = genre_boost(item_genres, genre_boosts) + _word_match(item, query_words)
            taste_bonus, own_bonus, other_bonus = self._taste_bonus(
                item_genres, item_keywords, own_taste, other_taste, mix
            )

            cv = coview.get(item.get("id")) or {}
            tier = cv.get("tier", "C")
            tier_boost = TIER_BOOSTS.get(tier, 0.0)
            theme_boost = _concept_adjustment(item, item_genres, requested_themes) if concept_requested else 0.0

            final_score = base_score + taste_bonus + tier_boost + relevance_boost + theme_boost

            item["recommendation_score"] = round(final_score, 2)
            item["base_weighted_rating"] = round(base_score, 2)
            item["taste_bonus"] = round(taste_bonus, 2)
            item["taste_bonus_own"] = round(own_bonus, 2)
            item["taste_bonus_other"] = round(other_bonus, 2)
            item["co_view_tier"] = tier
            item["tier_boost"] = tier_boost
            item["relevance_boost"] = round(relevance_boost, 2)
            if cv.get("combined_score") is not None:
                item["combined_score"] = cv["combined_score"]
            if cv.get("seen_by"):
                item["seen_by_profile_ids"] = cv["seen_by"]
            other_name = other_taste.get("name") or "They"
            if tier == "A":
                item["tier_reason"] = "You both liked it"
            elif tier == "B":
                item["tier_reason"] = f"{other_name} loved this — you haven't"

            scored.append((final_score, item))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [item for _, item in scored]

    def rank_candidates(
        self,
        candidates: List[Dict[str, Any]],
        taste_profile: Dict[str, Any],
        query: Optional[str] = None,
        concept_requested: bool = False,
        genre_boosts: Optional[Dict[str, float]] = None,
        content_words: Optional[List[str]] = None,
        requested_themes: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Cold start (nobody has rated anything): Bayesian rating, the old summed
        taste profile, and the same request signals as the profile-aware path."""
        preferred_genres = taste_profile.get("preferred_genres", {})
        preferred_keywords = taste_profile.get("preferred_keywords", {})

        scored: List[Tuple[float, Dict[str, Any]]] = []
        for item in candidates:
            raw_rating = float(item.get("rating") or 6.0)
            num_votes = int(item.get("num_votes") or item.get("vote_count") or 100)

            # 1. Base Score: Bayesian Weighted Rating
            base_score = calculate_weighted_rating(raw_rating, num_votes)

            # 2. Taste Affinity Score
            taste_bonus = 0.0
            item_genres = set(_split_lower(item.get("genres")))
            item_keywords = set(_split_lower(item.get("tier2_keywords")))

            for g in item_genres:
                if g in preferred_genres:
                    taste_bonus += preferred_genres[g] * 0.2
            for k in item_keywords:
                if k in preferred_keywords:
                    taste_bonus += preferred_keywords[k] * 0.15

            taste_bonus = min(taste_bonus, 2.0)

            # 3. The request: genres asked for and words in the synopsis.
            relevance_boost = genre_boost(item_genres, genre_boosts) + _word_match(item, set(content_words or []))
            theme_boost = _concept_adjustment(item, item_genres, requested_themes) if concept_requested else 0.0

            final_score = base_score + taste_bonus + relevance_boost + theme_boost

            item["recommendation_score"] = round(final_score, 2)
            item["base_weighted_rating"] = round(base_score, 2)
            item["taste_bonus"] = round(taste_bonus, 2)
            item["relevance_boost"] = round(relevance_boost, 2)

            scored.append((final_score, item))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [item for _, item in scored]

    def apply_mmr_diversity(
        self, candidates: List[Dict[str, Any]], top_n: int, lambda_param: float = 0.7
    ) -> List[Dict[str, Any]]:
        """Maximal Marginal Relevance (MMR) re-ranking to maximize diversity and remove duplicates."""
        if not candidates or top_n <= 0:
            return []

        # Genre sets and title stems computed once: the pool is now 200, and
        # re-splitting them inside the double loop made MMR the slowest step.
        genres_of = [frozenset(_split_lower(c.get("genres"))) for c in candidates]
        stem_of = [(c.get("title") or "").split(":")[0] for c in candidates]
        # max_sim[i]: item i's highest similarity to anything selected so far,
        # updated incrementally as each pick is made.
        max_sim = [0.0] * len(candidates)
        remaining = list(range(len(candidates)))
        selected: List[Dict[str, Any]] = []

        while remaining and len(selected) < top_n:
            best = max(remaining, key=lambda i: (
                lambda_param * (candidates[i].get("recommendation_score") or 0.0)
                - (1 - lambda_param) * max_sim[i], -i))
            remaining.remove(best)
            selected.append(candidates[best])
            for i in remaining:
                sim = jaccard_similarity(genres_of[i], genres_of[best])
                # Deduplicate franchises/same main title stems
                if stem_of[i] == stem_of[best]:
                    sim += 0.5
                if sim > max_sim[i]:
                    max_sim[i] = sim

        return selected

    def recommend(
        self,
        mood_id: int = 8,
        exclude_watched: bool = True,
        era_preference: Optional[str] = None,
        duration_preference: Optional[str] = None,
        page: int = 1,
        page_size: int = 10,
        sub_mood: Optional[str] = None,
        query: Optional[str] = None,
        media_type: str = "movie",
        genres_override: Optional[List[str]] = None,
        min_rating_override: Optional[float] = None,
        variety: bool = False,
        profile: str = "together",
        pool_hook: Optional[Any] = None,
        return_pool: bool = False,
        only_ids: Optional[List[int]] = None,
        remember_shown: bool = False,
        skip_session_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Executes multi-stage filtering, scoring, MMR diversity pruning, and pagination.

        only_ids limits the first candidate query to these catalogue ids (the Northern Lights
        "For both of us" row ranks only titles that are in the library).

        pool_hook(ranked) -> ranked re-scores the scored pool before series
        order and diversity (the questions pop-up applies its answers here).
        return_pool=True stops there and returns {"pool": ranked, ...}, so
        the pop-up can re-rank the same pool after every answer without
        running SQL again (see `finalize`).

        remember_shown=True pushes down titles Discover showed lately that
        nobody opened (skip_session_id: the session being paged through).
        """
        mood_info = MOOD_MAPPINGS.get(mood_id, MOOD_MAPPINGS[8])

        # The server fills genres_override with the mood card's genres when no
        # chip was tapped; those are the mood's, not a choice of genre.
        chip_genres = genres_override
        if chip_genres is not None and list(chip_genres) == list(mood_info["genres"]):
            chip_genres = None
        genre_spec = resolve_effective_genres(mood_info["genres"], chip_genres, query)
        genres = genre_spec["include"] or None
        excluded_genres = genre_spec["exclude"]
        content_words = genre_spec["content_words"]
        concept_requested = genre_spec.get("concept_requested", False)
        genre_boosts = dict(genre_spec["boost"])
        # Premise terms for the SQL shortlist: the concrete phrases from the
        # themes, never the abstract request words ("concept", "interpret").
        concept_terms: List[str] = []
        if concept_requested:
            concept_terms = list(dict.fromkeys(t.strip() for spec in THEMES.values()
                                               for t in spec["terms"] if t.strip()))

        # Origin-of-cinema requests ("South Asian movies") are places, not
        # genres. Filter on country AND language so TMDB's origin mislabels
        # (Point Break filed as IN) don't leak Hollywood back in.
        origin_spec = parse_origin_intent(query)
        origin_countries = origin_spec["countries"] if origin_spec else None
        origin_languages = origin_spec["languages"] if origin_spec else None
        if origin_spec:
            print(
                f"[Recommender] Origin request '{origin_spec['label']}': "
                f"countries={origin_countries} languages={origin_languages}",
                flush=True,
            )
        # Foreign-language titles only when top tier, unless the request named a
        # place or a language, said "foreign", or ranks the people's own library.
        gate = None
        if not origin_spec and not genre_spec.get("any_language") and only_ids is None:
            gate = foreign_gate(RULES, media_type)

        def with_tv_aliases(names: Optional[List[str]]) -> Optional[List[str]]:
            if media_type != "tv" or not names:
                return names
            out = list(names)
            for g in names:
                out.extend(TV_GENRE_ALIASES.get(g.strip().lower(), []))
            return list(dict.fromkeys(out))

        genres = with_tv_aliases(genres)
        excluded_genres = with_tv_aliases(excluded_genres)

        min_rating = float(_W["min_rating"]) if min_rating_override is None else min_rating_override
        if query and any(kw in query.lower() for kw in GUILTY_PLEASURE_KEYWORDS):
            min_rating = 0.0

        # NB: the SQL `exclude_watched` drops anything EITHER profile has
        # watched. In a profile-aware run that would delete every rated film
        # before the scorer could see its tier, making Tier A unreachable — so
        # watched exclusion is handled in Python below, where "seen by both"
        # can be distinguished from "seen by one".
        sql_exclude_watched = exclude_watched and profile not in PROFILE_MIX

        def fetch(**over: Any) -> List[Dict[str, Any]]:
            """The quality-cut funnel: hard filters + Bayesian ordering in SQL."""
            args = dict(
                media_type=media_type,
                genres=genres or None,
                exclude_genres=excluded_genres or None,
                origin_countries=origin_countries,
                origin_languages=origin_languages,
                exclude_watched=sql_exclude_watched,
                min_rating=min_rating,
                era=era_preference,
                duration=duration_preference if media_type == "movie" else None,
                foreign_gate=gate,
                limit=self.shortlist_size,
            )
            args.update(over)
            return self.db.get_top_candidates(**args)

        candidates = fetch(concept_terms=concept_terms, only_ids=only_ids)
        if not candidates and genres:
            # A named genre the catalogue has nothing for (TMDB's TV genres have
            # no Horror): keep it as a strong pull instead of returning nothing.
            for g in genre_spec["include"]:
                genre_boosts[g.strip().lower()] = float(_W["hinted_genre"])
            genres = None
            candidates = fetch(concept_terms=concept_terms, only_ids=only_ids)

        def merge(extra: List[Dict[str, Any]]) -> None:
            nonlocal candidates
            seen_ids = {c.get("id") for c in candidates}
            candidates = list(candidates) + [c for c in extra if c.get("id") not in seen_ids]

        # Genres the request hinted at ("make me laugh"): their best titles join
        # the shortlist, so the pull has films to pull. The general quality cut
        # alone holds few of any one genre.
        for g in genre_spec.get("hinted") or []:
            if g in (genre_spec["include"] or []):
                continue
            merge(fetch(genres=with_tv_aliases([g]), limit=int(_W["hinted_shortlist"]), only_ids=only_ids))

        # A concept request must not be decided by the quality cut alone: a
        # premise-relevant film rated 7.3 loses to hundreds of better-rated ones.
        if concept_requested:
            merge(fetch(concept_terms=concept_terms, limit=self.shortlist_size * 2, only_ids=only_ids))

        # Vibe search: the titles whose vibe documents are closest to the
        # typed request join the shortlist, through the same hard filters.
        vibe = self._vibe_query(query) if self.vibe_ready(query) else None
        if vibe:
            near = [i for i, _ in self.vibe_index.top(vibe["vector"], k=VIBE_SHORTLIST)]
            if only_ids is not None:
                allowed = {int(i) for i in only_ids}
                near = [i for i in near if int(i) in allowed]
            merge(fetch(limit=VIBE_SHORTLIST, only_ids=near))

        if not candidates:
            return {"total_results": 0, "page": page, "page_size": page_size, "items": []}

        # Era & Runtime Filters
        if era_preference == "modern":
            candidates = [c for c in candidates if (c.get("release_year") or 0) >= 2015] or candidates
        elif era_preference == "2000s":
            candidates = [c for c in candidates if 2000 <= (c.get("release_year") or 0) < 2015] or candidates
        elif era_preference == "classic":
            candidates = [c for c in candidates if (c.get("release_year") or 0) < 2000] or candidates

        if media_type == "movie" and duration_preference:
            if duration_preference == "short":
                candidates = [c for c in candidates if 0 < (c.get("runtime") or 0) < 105] or candidates
            elif duration_preference == "epic":
                candidates = [c for c in candidates if (c.get("runtime") or 0) > 140] or candidates

        # Profile-aware exclusion, applied in Python so "seen by both" can be told
        # apart from "seen by one".
        #
        # In EVERY profile mode, a film the viewer has already watched must not
        # be recommended back to them. `together` previously excluded only what
        # BOTH had seen, so a film only she had watched surfaced with a "she
        # hasn't seen this yet" style tier C badge — which is wrong: in
        # together mode the point is finding something neither has watched.
        # Tier B ("he loved it, you haven't") is for the RATING queue, which
        # decides what he should watch, not what the pair should watch.
        if exclude_watched and profile in PROFILE_MIX:
            coview_all = self.db.get_item_co_view(
                [c["id"] for c in candidates if c.get("id")], viewer_profile_id=None
            )
            candidates = [
                c for c in candidates
                if len((coview_all.get(c["id"]) or {}).get("seen_by") or []) == 0
            ]
            if not candidates:
                return {"total_results": 0, "page": page, "page_size": page_size, "items": []}

        # Stage 2: Personal Taste & Co-view Scoring
        # Profile-aware path blends the two people's tastes and applies tier
        # boosts; the legacy path is unchanged when no profile signal exists.
        has_profile_signal = any(
            t.get("rating_count", 0) > 0 for t in self.db.get_all_profile_tastes().values()
        )
        if has_profile_signal:
            ranked = self.rank_candidates_profile_aware(
                candidates, profile=profile, query=query,
                content_words=content_words,
                genre_boosts=genre_boosts,
                concept_requested=concept_requested,
                requested_themes=genre_spec.get("themes"),
            )
        else:
            taste = self.db.get_user_taste_profile(min_rating=7.0)
            ranked = self.rank_candidates(
                candidates, taste, query=query,
                concept_requested=concept_requested,
                genre_boosts=genre_boosts, content_words=content_words,
                requested_themes=genre_spec.get("themes"),
            )
            for item in ranked:
                item["co_view_tier"] = "C"
                item["tier_boost"] = 0.0

        if not ranked:
            return {"total_results": 0, "page": page, "page_size": page_size, "items": []}

        if vibe:
            ranked = self._apply_vibe(ranked, vibe)
        if self.taste_runtime is not None:
            ranked = self.taste_runtime.apply(ranked, profile)
        if remember_shown:
            ranked = self._apply_memory(ranked, skip_session_id)
        if pool_hook is not None:
            ranked = pool_hook(ranked)

        vibe_info = {"used": bool(vibe), "like_titles": [t["title"] for t in vibe["like"]] if vibe else []}
        if return_pool:
            return {"pool": ranked, "vibe": vibe_info}

        # Cold-start variety is suppressed when the request named an origin or
        # genre. Those are targeted queries where the user asked for a specific
        # shelf, and shuffling threw away the quality ordering: "bangladeshi
        # movies" surfaced 2-vote films above The Clay Bird and Monpura because
        # the shuffle, not the Bayesian score, decided the page.
        targeted = bool(
            origin_spec
            or genre_spec["include"]
            or genre_spec["hinted"]
            or genre_spec["exclude"]
            or genre_spec.get("weight", 0) > 0
            or bool(vibe)   # a vibe-ranked list is a targeted answer too
            or pool_hook is not None
        )
        result = self.finalize(ranked, page, page_size, shuffle=variety and not targeted,
                               spread_exempt=set(genre_spec["include"]) | set(genre_spec["hinted"]))
        result["vibe"] = vibe_info
        return result

    def finalize(self, ranked: List[Dict[str, Any]], page: int = 1, page_size: int = 10,
                 shuffle: bool = False, spread_exempt: Optional[Set[str]] = None) -> Dict[str, Any]:
        """Series order, diversity and the page slice for an already-scored pool.

        spread_exempt: genres the request asked for, which the genre spread
        never holds back."""
        if not ranked:
            return {"total_results": 0, "page": page, "page_size": page_size, "items": [], "has_more": False}

        # Stage 2b: Series order. A sequel is swapped for the earliest entry
        # neither of you has seen, and each series appears at most once.
        ranked = apply_series_order(ranked, self.db)

        # Stage 3: Candidate Diversity Pruning (MMR)
        # Process the shortlist through MMR to build the diverse final pool.
        pool_size = min(len(ranked), self.final_pool)
        diverse_candidates = self.apply_mmr_diversity(ranked[:pool_size], top_n=pool_size)

        # Cold-start variety (opt-in): seeded shuffle that rotates every 10
        # minutes — repeat requests surface different picks, but the order is
        # stable within a bucket so pagination stays coherent while browsing.
        if shuffle and len(diverse_candidates) > page_size:
            random.Random(int(time.time() // 600)).shuffle(diverse_candidates)

        # Stage 3b: no shelf (animation, horror, ...) fills a page unless asked.
        diverse_candidates = apply_genre_spread(diverse_candidates, spread_exempt)

        # Stage 4: Pagination Slice
        total_results = len(diverse_candidates)
        start_idx = (page - 1) * page_size
        end_idx = start_idx + page_size
        paginated_items = diverse_candidates[start_idx:end_idx]

        return {
            "total_results": total_results,
            "page": page,
            "page_size": page_size,
            "has_more": end_idx < total_results,
            "items": paginated_items,
        }
