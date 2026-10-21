"""
Recommender rules (services/recommender_rules.py)

Loads recommender_rules.yaml: the word lists, mood cards, place names and tuning
numbers the recommender uses. The file is the only place these choices live; the
recommender code reads them from here. RECOMMENDER_RULES_PATH points at another copy.

A missing or broken file never stops the server: the recommender then runs with no
word lists (every request word is matched against synopses) and neutral weights,
and the problem is printed.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

_DEFAULT_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "recommender_rules.yaml")

# Used only for keys the file leaves out, so a partial file still works.
_FALLBACK: Dict[str, Any] = {
    "foreign_titles": {"home_languages": [], "min_rating": 0.0, "min_votes": {}, "any_language_words": []},
    "genre_names": {}, "genre_hints": {}, "tones": {}, "guilty_pleasure": [],
    "concept_request": {"words": [], "penalty_genres": [], "penalty": 0.0, "hint_weight": 1.0,
                        "theme_boost": 0.0, "theme_boost_more": 0.0, "theme_boost_max": 0.0, "themes": {}},
    "moods": {8: {"name": "Surprise Me!", "vibe": "anything goes", "genres": []}},
    "choose_defaults": [],
    "weights": {"min_rating": 6.0, "hinted_genre": 2.0, "named_genre": 1.0, "tone_genre": 0.5,
                "word_match": 0.3, "word_match_max": 2.0, "vibe": 3.0, "vibe_shortlist": 400,
                "hinted_shortlist": 300},
    "profile_mix": {"together": [0.6, 0.4], "me": [0.75, 0.25], "partner": [0.75, 0.25]},
    "tier_boosts": {"A": 1.5, "B": 0.8, "C": 0.0},
    "memory": {"ignored": 1.0, "ignored_cap": 3.0, "ignored_days": 14, "dismissed": 5.0, "dismissed_days": 30},
    "taste": {"genre_shrink": 3.0, "mean_shrink": 5.0, "keyword_shrink": 2.0, "keyword_min": 1,
              "genre_scale": 4.0, "keyword_scale": 3.0, "keyword_full": 3},
    "spread": {"shelves": [], "max": 2, "window": 10},
    "origins": {},
    "tv_genre_aliases": {},
}


def rules_path() -> str:
    return os.getenv("RECOMMENDER_RULES_PATH") or _DEFAULT_PATH


def load_rules(path: Optional[str] = None) -> Dict[str, Any]:
    """The rules file merged over the fallback (one level deep for the dict sections)."""
    path = path or rules_path()
    raw: Dict[str, Any] = {}
    try:
        import yaml
        with open(path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        if not isinstance(raw, dict):
            raise ValueError("the file is not a mapping")
    except Exception as e:  # noqa: BLE001 - a bad rules file must not stop the server
        print(f"[Recommender] Could not read {path} ({e}); running without recommender rules.", flush=True)
        raw = {}
    out: Dict[str, Any] = {}
    for key, fallback in _FALLBACK.items():
        value = raw.get(key, fallback)
        if isinstance(fallback, dict) and isinstance(value, dict) and key not in ("moods", "origins", "genre_names",
                                                                                 "genre_hints", "tones",
                                                                                 "tv_genre_aliases"):
            value = {**fallback, **value}
        out[key] = value
    return out


def word_index(by_genre: Dict[str, List[str]]) -> Dict[str, List[str]]:
    """{genre: [words]} -> {word: [genres]} (a word may name more than one genre: "romcom")."""
    out: Dict[str, List[str]] = {}
    for genre, words in (by_genre or {}).items():
        for w in words or []:
            key = str(w).strip().lower()
            if key and genre not in out.setdefault(key, []):
                out[key].append(genre)
    return out


def passes_foreign_gate(item: Dict[str, Any], media_type: str, gate: Optional[Dict[str, Any]]) -> bool:
    """True when the title is in a home language, has no language on record, or is top tier."""
    if not gate:
        return True
    lang = str(item.get("original_language") or "").strip().lower()
    if not lang or lang in gate["home_languages"]:
        return True
    return (float(item.get("rating") or 0.0) >= gate["min_rating"]
            and int(item.get("vote_count") or 0) >= gate["min_votes"])


def foreign_gate(rules: Dict[str, Any], media_type: str) -> Optional[Dict[str, Any]]:
    """The gate for this media type, or None when no home language is configured."""
    cfg = rules.get("foreign_titles") or {}
    home = [str(x).strip().lower() for x in cfg.get("home_languages") or [] if str(x).strip()]
    if not home:
        return None
    votes = cfg.get("min_votes") or {}
    return {
        "home_languages": home,
        "min_rating": float(cfg.get("min_rating") or 0.0),
        "min_votes": int(votes.get(media_type, votes.get("movie", 0)) if isinstance(votes, dict) else votes),
    }


RULES: Dict[str, Any] = load_rules()
