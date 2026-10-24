"""
The adapter contract: everything Wishmatch needs from a project.

A project (films, music, food) writes one class with these methods. Wishmatch
calls nothing else, so the package never imports project code.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Protocol, Set

from .types import Item, Reaction, Request, Vocabulary


class Adapter(Protocol):
    def vocabulary(self) -> Vocabulary:
        """The allowed trait and tag words."""

    def items(self, request: Request) -> Iterable[Item]:
        """Every item that may be picked for this request, already described, with the
        project's hard filters applied (already seen, wrong language, ...). When
        request.only_ids is set, return only those items: the engine then skips its own
        familiarity and pool-size cuts and ranks exactly what it is given."""

    def describe(self, item_ids: List[Any]) -> Dict[Any, Item]:
        """These items described (used for the items people reacted to)."""

    def reactions(self, person: Any) -> List[Reaction]:
        """What this person liked or disliked, -1..+1."""

    def profile(self, person: Any) -> Optional[Dict[str, Any]]:
        """OPTIONAL. What this person says they like and avoid: {"likes": [tag words], "dislikes": [tag words]},
        or None. Counts at settings["taste"]["declared_weight"] even before they have any reactions."""

    def seen_by(self, person: Any) -> Set[Any]:
        """OPTIONAL. Items this person has already seen. Watching together, an item some of
        the people have seen is pushed down by settings["people"]["seen_penalty"]."""

    def crowd(self, item_ids: List[Any]) -> Dict[Any, Dict[Any, float]]:
        """For each of these items, the items its fans also liked: {item: {other item: similarity 0..1}}.
        Return {} if there is no crowd data."""

    def rules(self) -> Dict[str, Any]:
        """The settings (weights, pool sizes, word lookup). See settings.DEFAULTS for the keys."""
