"""
Northern Lights reactions (M9c, W10): after finishing, the same five verdicts as the Rate page.

There is no rating system of its own here. Each answer is one of the existing verdicts (services/verdicts.py,
handover section 10.7) and is written with `db.rate_item(verdict=...)` for the profile(s) of whoever is at the
screen, so the recommender, the Rate tab and the taste scores all see it like any other rating.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

# One vocabulary and order for the Rate page, legacy finish modal, and player.
# The old player reaction IDs remain accepted below for already-open clients.
from services.verdicts import VERDICTS

REACTIONS: List[Dict[str, str]] = [
    {"id": verdict, "label": data["label"], "verdict": verdict}
    for verdict, data in VERDICTS.items()
]
_BY_ID = {r["id"]: r for r in REACTIONS}
_LEGACY_IDS = {"fine": "meh", "not_for_us": "not_for_me"}


def verdict_for(reaction: Any) -> Optional[str]:
    """The verdict an answer stands for, or None for anything unknown."""
    reaction_id = str(reaction or "").strip().lower()
    reaction_id = _LEGACY_IDS.get(reaction_id, reaction_id)
    r = _BY_ID.get(reaction_id)
    return r["verdict"] if r else None


def question(title: str, both: bool) -> str:
    """The line above the buttons."""
    title = (title or "").strip()
    if both:
        return f"How was {title} for the two of you?" if title else "How was it for the two of you?"
    return f"How was {title}?" if title else "How was it?"


def profiles_to_rate(profile_ids: Sequence[Optional[int]], implicit: bool) -> List[int]:
    """Whose profiles get the rating: the people at the screen; the single implicit viewer is profile 1."""
    ids = [p for p in profile_ids if p is not None]
    return ids or ([1] if implicit else [])


def has_own_verdict(details: Optional[Dict[str, Any]]) -> bool:
    """True when a person already gave this title a verdict themselves (a guessed one does not count)."""
    return bool(details and details.get("verdict") and not details.get("inferred"))
