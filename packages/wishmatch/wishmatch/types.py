"""
The few shapes Wishmatch works with.

Wishmatch never knows what an item is (a film, a song, a dish). It sees:
- traits: named scales from 0 to 1 ("energy", "humour"),
- tags: words from closed lists ("cozy", "date night"),
- reactions: how much a person liked an item, from -1 to +1.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set


@dataclass
class Vocabulary:
    """The words an adapter allows. Wishes and item descriptions use only these."""
    traits: Dict[str, Dict[str, str]]          # name -> {"low": "...", "high": "..."}
    tags: Dict[str, List[str]]                 # group -> words ("feels" -> ["cozy", ...])

    def all_tags(self) -> Set[str]:
        return {t for words in self.tags.values() for t in words}


@dataclass
class Item:
    """One thing that can be recommended, as the adapter describes it."""
    id: Any
    kind: str                                  # the adapter's own types: "movie", "tv", "album"
    traits: Dict[str, float] = field(default_factory=dict)
    tags: Set[str] = field(default_factory=set)
    vibe: str = ""                             # one line, shown to people
    known: str = "well"                        # how well the describer knew it: well | somewhat | not
    popularity: float = 0.0                    # any number where higher = more widely known (votes)
    quality: float = 0.5                       # 0..1, the crowd's opinion (a rating scaled down)
    extra: Dict[str, Any] = field(default_factory=dict)   # passed through untouched


@dataclass
class Wish:
    """What a request asks for, in vocabulary words."""
    text: str = ""
    traits: Dict[str, float] = field(default_factory=dict)        # trait -> wanted value 0..1
    tags: Set[str] = field(default_factory=set)                   # tags wanted
    tag_sets: List[Set[str]] = field(default_factory=list)        # one set per understood phrase; any tag in it satisfies it
    avoid_tags: Set[str] = field(default_factory=set)             # tags not wanted
    words_used: List[str] = field(default_factory=list)           # request words that were understood
    words_unknown: List[str] = field(default_factory=list)        # request words that were not
    hidden_gems: bool = False                                     # include little-known items
    source: str = "lookup"                                        # who read it: lookup | model | model+lookup

    def is_empty(self) -> bool:
        return not (self.traits or self.tags or self.avoid_tags)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "text": self.text,
            "traits": {k: round(v, 2) for k, v in self.traits.items()},
            "tags": sorted(self.tags),
            "tag_sets": [sorted(t) for t in self.tag_sets],
            "avoid_tags": sorted(self.avoid_tags),
            "words_used": self.words_used,
            "words_unknown": self.words_unknown,
            "hidden_gems": self.hidden_gems,
            "source": self.source,
        }


@dataclass
class Reaction:
    """How much a person liked an item: -1 (hated) to +1 (loved)."""
    item_id: Any
    score: float


@dataclass
class Request:
    """One ask. `people` are the adapter's person ids; the first is the viewer."""
    text: str = ""
    people: List[Any] = field(default_factory=list)
    together: bool = False                     # True = everyone in `people` watches together
    kinds: Optional[List[str]] = None          # None = every kind the settings list
    only_ids: Optional[List[Any]] = None       # rank only these items
    filters: Dict[str, Any] = field(default_factory=dict)   # adapter-specific, passed through
    limit: int = 20


@dataclass
class Pick:
    """One result: the item, its score and the words that explain it."""
    item: Item
    score: float
    match: float
    taste: float
    reasons: List[str] = field(default_factory=list)
    crowd: float = 0.0                         # -1..1, liked by fans of what the people liked
    because: Any = None                        # the liked item that pulled this in most (crowd), if any
