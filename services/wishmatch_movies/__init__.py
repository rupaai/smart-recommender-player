"""
Wishmatch for films and shows (PROJECT_HANDOVER.md §17).

The reusable engine lives in packages/wishmatch and knows nothing about films;
this folder is its movie adapter: the catalogue, the Haiku mood tags, the mood
vocabulary, the ratings and the foreign-title rule.
"""

from __future__ import annotations

import os
import sys

# packages/wishmatch is not installed as a dependency (no lock-file change); put it on the path.
_PKG = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "packages", "wishmatch")
if _PKG not in sys.path:
    sys.path.insert(0, _PKG)

from .adapter import MovieAdapter  # noqa: E402
from .router import EngineSwitch  # noqa: E402
from .service import WishmatchMovies  # noqa: E402

__all__ = ["MovieAdapter", "EngineSwitch", "WishmatchMovies"]
