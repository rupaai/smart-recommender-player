"""
Reading a wish with a plain word lookup.

Typed words are matched, longest phrase first, against:
- the lexicon's trait words ("relaxing" -> energy 0.2, tension 0.1),
- the lexicon's tag words ("make me laugh" -> silly, witty),
- the vocabulary's own tag words ("cozy", "date night").
A negator just before a match ("not scary", "no gore") flips it: a trait's wanted
value becomes 1 - value and a tag becomes a tag to avoid.

A smarter reader (a language model) can replace this later; it must return the same Wish.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set, Tuple

from .types import Vocabulary, Wish

_TOKEN = re.compile(r"[a-z0-9]+(?:['’-][a-z0-9]+)*")
_MAX_PHRASE = 5


def tokens(text: str) -> List[str]:
    return [t.replace("’", "'") for t in _TOKEN.findall((text or "").lower())]


def _phrase_key(text: str) -> str:
    return " ".join(tokens(text))


def _tag_phrases(vocab: Vocabulary) -> Dict[str, List[str]]:
    """Each vocabulary tag as a phrase, plus its form without a leading article ("a bad day" -> "bad day")."""
    out: Dict[str, List[str]] = {}
    for tag in vocab.all_tags():
        key = _phrase_key(tag)
        out.setdefault(key, []).append(tag)
        for lead in ("a ", "an ", "the ", "when you're "):
            if key.startswith(lead):
                out.setdefault(key[len(lead):], []).append(tag)
    return out


def _lookup(lexicon: Dict[str, Any], vocab: Vocabulary) -> Dict[str, Tuple[Dict[str, float], List[str]]]:
    """phrase -> (trait targets, tags), from the lexicon and the vocabulary's own tags."""
    allowed_traits = set(vocab.traits)
    allowed_tags = vocab.all_tags()
    table: Dict[str, Tuple[Dict[str, float], List[str]]] = {}

    def entry(key: str) -> Tuple[Dict[str, float], List[str]]:
        return table.setdefault(key, ({}, []))

    for key, tags in _tag_phrases(vocab).items():
        entry(key)[1].extend(tags)
    for phrase, targets in (lexicon.get("traits") or {}).items():
        key = _phrase_key(str(phrase))
        for trait, value in (targets or {}).items():
            if trait in allowed_traits:
                entry(key)[0][trait] = max(0.0, min(1.0, float(value)))
    for phrase, tags in (lexicon.get("tags") or {}).items():
        key = _phrase_key(str(phrase))
        for tag in tags or []:
            if tag in allowed_tags and tag not in entry(key)[1]:
                entry(key)[1].append(tag)
    return {k: v for k, v in table.items() if k and (v[0] or v[1])}


def read_wish(text: str, vocab: Vocabulary, lexicon: Optional[Dict[str, Any]] = None) -> Wish:
    """The wish in vocabulary words. Words that match nothing are listed in words_unknown."""
    lexicon = lexicon or {}
    table = _lookup(lexicon, vocab)
    negators = {str(w).lower() for w in lexicon.get("negators") or []}
    stop = {str(w).lower() for w in lexicon.get("stop_words") or []}
    gem_phrases = [_phrase_key(str(w)) for w in lexicon.get("hidden_gem_words") or []]

    toks = tokens(text)
    wish = Wish(text=text or "")
    joined = " ".join(toks)
    wish.hidden_gems = any(p and re.search(rf"\b{re.escape(p)}\b", joined) for p in gem_phrases)

    trait_votes: Dict[str, List[float]] = {}
    used: Set[int] = set()
    last_end = 0
    i = 0
    while i < len(toks):
        hit = None
        for n in range(min(_MAX_PHRASE, len(toks) - i), 0, -1):
            key = " ".join(toks[i:i + n])
            if key in table:
                hit = (key, n)
                break
        if not hit:
            i += 1
            continue
        key, n = hit
        # A negator counts only for the next match: "not cozy, funny" avoids cozy, wants funny.
        negated = any(t in negators for t in toks[max(last_end, i - 2):i])
        targets, tags = table[key]
        for trait, value in targets.items():
            trait_votes.setdefault(trait, []).append(1.0 - value if negated else value)
        for tag in tags:
            (wish.avoid_tags if negated else wish.tags).add(tag)
        if tags and not negated and set(tags) not in wish.tag_sets:
            wish.tag_sets.append(set(tags))
        wish.words_used.append(("not " if negated else "") + key)
        used.update(range(i, i + n))
        i += n
        last_end = i

    wish.traits = {t: sum(v) / len(v) for t, v in trait_votes.items()}
    wish.tags -= wish.avoid_tags
    wish.tag_sets = [ts - wish.avoid_tags for ts in wish.tag_sets if ts - wish.avoid_tags]
    gem_words = {w for p in gem_phrases for w in p.split()}
    wish.words_unknown = [t for j, t in enumerate(toks)
                          if j not in used and t not in negators and t not in stop and t not in gem_words]
    return wish
