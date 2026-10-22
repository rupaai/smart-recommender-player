"""Reason words shown under a pick: what matched the wish first, then what fits the taste."""

from __future__ import annotations

from typing import List


def reasons(match_words: List[str], taste_words: List[str], limit: int = 3) -> List[str]:
    out: List[str] = []
    for word in list(match_words) + list(taste_words):
        if len(out) >= limit:
            break
        if word and word not in out:
            out.append(word)
    return out
