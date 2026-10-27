"""Wishmatch: recommend items from a typed wish, item descriptions and people's tastes.

The package knows nothing about any one project. A project supplies an adapter
(see adapter.py); everything else is here.
"""

from .adapter import Adapter
from .engine import Wishmatch, mix_kinds
from .llm import LLMWishReader, word_question
from .ollama import OllamaClient
from .questions import apply_answer, next_question
from .settings import DEFAULTS, merged
from .types import Item, Pick, Reaction, Request, Vocabulary, Wish
from .wish import read_wish
from .llm import AnswerFile  # noqa: E402

__all__ = ["Adapter", "Wishmatch", "mix_kinds", "LLMWishReader", "word_question", "OllamaClient", "apply_answer", "next_question", "DEFAULTS", "merged", "Item", "Pick", "Reaction",
           "Request", "Vocabulary", "Wish", "read_wish"]
__version__ = "0.1.0"
__all__ += ["AnswerFile"]
