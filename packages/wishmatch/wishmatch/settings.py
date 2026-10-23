"""
Settings: every number and choice Wishmatch uses, with neutral defaults.

The adapter's rules() returns a dict laid over these defaults (one level deep),
so a project only writes what it changes.
"""

from __future__ import annotations

import copy
from typing import Any, Dict

DEFAULTS: Dict[str, Any] = {
    # How many candidates are scored, and how they split between kinds when no kind is chosen.
    "candidates": {
        "total": 2000,
        "kinds": {},                 # kind -> share of the pool, e.g. {"movie": 0.7, "tv": 0.3}
        "known": ["well", "somewhat"],
        "min_popularity": {},        # known level -> minimum popularity, e.g. {"somewhat": 500}
        "hidden_gem_known": ["somewhat", "not"],
    },
    # Share of each kind in the results when no kind is chosen (the rest is filled by score).
    "mix": {},
    # How the final score is put together.
    "weights": {
        "match": 1.0,                # how well the item fits the wish (0..1)
        "taste": 0.6,                # how well it fits the people's taste (-1..1)
        "quality": 0.3,              # the crowd's opinion (0..1)
        "crowd": 0.0,                # liked by similar people (0..1)
        "trait": 1.0,                # inside the match: a trait the wish names
        "tag": 1.0,                  # inside the match: a tag the wish names
        "avoid_tag": 1.0,            # inside the match: a tag the wish does not want
    },
    # The crowd (crowd.py): items that fans of what the people liked also liked.
    "crowd": {
        "shrink": 1.0,               # similarity needed before a crowd score counts fully
        "extra_candidates": 300,     # strongest crowd suggestions added to the pool beyond the popularity cut
        "min_score": 0.3,            # crowd score an item needs to be added that way
    },
    # Whose taste counts and how much.
    "people": {
        "other_weight": 0.2,         # alone: the other person's taste at this weight
        "together_dislike": 0.5,     # together: push down by this x how much the least keen person dislikes it
    },
    # Taste profile from reactions.
    "taste": {
        "shrink": 5.0,               # reactions needed before a taste counts fully
        "tag_shrink": 3.0,           # reactions with a tag needed before that tag counts fully
        "trait_weight": 0.5,         # share of the taste score from traits (the rest from tags)
    },
    # Reason words shown under each pick.
    "explain": {"max_reasons": 3, "hide_groups": []},   # hide_groups: vocabulary tag groups never shown as reasons
    # Word lookup that turns typed words into vocabulary words. See wish.py.
    "lexicon": {
        "traits": {},                # word or phrase -> {trait: wanted value}
        "tags": {},                  # word or phrase -> [tags]
        "negators": ["no", "not", "without", "nothing", "never", "less", "avoid", "don't", "dont", "isn't"],
        "hidden_gem_words": [],
        "stop_words": [],
    },
}


def merged(rules: Dict[str, Any]) -> Dict[str, Any]:
    """The defaults with `rules` laid over them, one level deep for dict sections."""
    out = copy.deepcopy(DEFAULTS)
    for key, value in (rules or {}).items():
        if isinstance(out.get(key), dict) and isinstance(value, dict):
            out[key] = {**out[key], **value}
        else:
            out[key] = value
    return out
