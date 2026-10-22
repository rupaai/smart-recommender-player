"""
The adapter contract: everything Wishmatch needs from a project.

A project (films, music, food) writes one class with these methods. Wishmatch
calls nothing else, so the package never imports project code.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Protocol

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

    def crowd(self, item_ids: List[Any]) -> Dict[Any, float]:
        """Items liked by people with similar taste, scored 0..1. Return {} if there is no crowd data."""

    def rules(self) -> Dict[str, Any]:
        """The settings (weights, pool sizes, word lookup). See settings.DEFAULTS for the keys."""
