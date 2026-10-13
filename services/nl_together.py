"""
Northern Lights: couple extras that need two people at the screen (services/nl_together.py), M9a

Pure functions (plain dicts in, plain values out), tested without a server:

  * `is_finished`   when a stop report counts as "finished the title" (seen together, reactions);
  * `pact_prompt`   U5, the "You said you'd watch this together. Watch anyway?" question.

With no people configured, or only one, there is nobody to watch together with: every function here
answers "nothing" and the pages hide the controls.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Sequence

FINISHED_FRACTION = 0.90        # Jellyfin itself counts a title as played from about 90 percent
PACT_TEXT = "You said you'd watch this together. Watch anyway?"


def is_finished(position_seconds: float, duration_seconds: Optional[float], ended: bool = False) -> bool:
    """A stop report counts as "finished" when the player reached the end, or stopped at 90 percent of
    the length or later (credits). Without a known length only the `ended` flag counts."""
    if ended:
        return True
    try:
        pos, dur = float(position_seconds or 0), float(duration_seconds or 0)
    except (TypeError, ValueError):
        return False
    return dur > 0 and pos >= FINISHED_FRACTION * dur


def together_possible(people: Sequence[Any]) -> bool:
    """Together features exist only when two or more people are configured."""
    return len(people) >= 2


def pact_prompt(
    viewer: Any,
    people: Sequence[Any],
    states: Dict[str, Dict[str, Any]],
    pact_on: bool,
    seen_together: bool,
) -> Optional[Dict[str, Any]]:
    """The question to ask before one person starts an episode alone, or None.

    Asked when: the show has the pact, one person (not "Both of us") is at the screen, they have not
    started or finished this episode, it was not seen together, and nobody else has already watched it
    (then there is nothing left to wait for). `states` is {person id: {"position": s, "played": bool}}
    as the resume rule reads it; a person we could not ask counts as "has not seen it"."""
    if not pact_on or seen_together or not together_possible(people):
        return None
    if getattr(viewer, "is_both", False) or getattr(viewer, "implicit", False):
        return None
    me = getattr(viewer, "primary", None)
    if me is None:
        return None
    mine = states.get(me.id) or {}
    if mine.get("played") or float(mine.get("position") or 0) > 0:
        return None
    others = [p for p in people if p.id != me.id]
    if any((states.get(p.id) or {}).get("played") for p in others):
        return None
    names = [p.name for p in others]
    return {
        "text": PACT_TEXT,
        "yes": "Watch anyway",
        "no": "Not now",
        "note": (names[0] + " hasn't seen it yet") if len(names) == 1 else None,
    }
