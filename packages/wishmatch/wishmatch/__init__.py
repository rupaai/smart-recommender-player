"""Wishmatch: recommend items from a typed wish, item descriptions and people's tastes.

The package knows nothing about any one project. A project supplies an adapter
(see adapter.py); everything else is here.
"""

from .adapter import Adapter
from .engine import Wishmatch, mix_kinds
from .settings import DEFAULTS, merged
from .types import Item, Pick, Reaction, Request, Vocabulary, Wish
from .wish import read_wish

__all__ = ["Adapter", "Wishmatch", "mix_kinds", "DEFAULTS", "merged", "Item", "Pick", "Reaction",
           "Request", "Vocabulary", "Wish", "read_wish"]
__version__ = "0.1.0"
