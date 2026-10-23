"""
Reading a wish (and wording questions) with a language model.

The model gets the allowed tag words and the typed wish, and answers JSON with the
words that fit and the words ruled out. It picks words only, no dials: on a small
processor every word the model writes costs time, so its answer is kept to a few words. Anything outside the
vocabulary is dropped. The plain word lookup (wish.py) always runs first; the model's
reading is laid over it, so words the lookup understood are kept, and when the model
is slow, missing or confused, the lookup's reading is used on its own.

`complete(prompt, schema, system) -> str | None` is any model call; OllamaClient.generate fits.
"""

from __future__ import annotations

import json
import threading
from collections import OrderedDict
from typing import Any, Callable, Dict, List, Optional

from .types import Vocabulary, Wish

Complete = Callable[..., Optional[str]]
NEGATORS = ("no ", "not ", "without", "n't", "never", "nothing", "avoid")


def _schema(vocab: Vocabulary, avoid_groups: Optional[List[str]] = None) -> Dict[str, Any]:
    tags = sorted(vocab.all_tags())
    avoid = sorted({t for g in avoid_groups for t in vocab.tags.get(g, [])}) if avoid_groups is not None else tags
    return {
        "type": "object",
        "properties": {
            "w": {"type": "array", "items": {"type": "string", "enum": tags}, "maxItems": 4},
            "a": {"type": "array", "items": {"type": "string", "enum": avoid or tags}, "maxItems": 3},
        },
        "required": ["w", "a"],
    }


def wish_system(vocab: Vocabulary, noun: str = "items") -> str:
    """The fixed part of the prompt, kept short. The model only picks words: every word it writes
    costs time on a small processor, and the mood dials come from the word lookup and the words."""
    groups = "\n".join(f"{g}: {', '.join(words)}" for g, words in vocab.tags.items())
    return (
        f"Pick words for a request for {noun}. Answer JSON: w = up to 4 words that fit the request, "
        "a = words it rules out with no / not / without (usually none). Use only these words:\n" + groups
    )


def wish_prompt(text: str) -> str:
    return text


def parse_answer(raw: Optional[str], vocab: Vocabulary) -> Optional[Wish]:
    """The model's JSON as a Wish in vocabulary words, or None if it isn't usable."""
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        start, end = raw.find("{"), raw.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            data = json.loads(raw[start:end + 1])
        except ValueError:
            return None
    if not isinstance(data, dict):
        return None
    tags = vocab.all_tags()
    wish = Wish(source="model")
    for trait, value in (data.get("s") or data.get("scales") or {}).items():
        if trait in vocab.traits and isinstance(value, (int, float)):
            wish.traits[trait] = max(0.0, min(1.0, float(value) / 10.0))
    wish.tags = {str(t) for t in data.get("w") or data.get("want") or [] if str(t) in tags}
    wish.avoid_tags = {str(t) for t in data.get("a") or data.get("avoid") or [] if str(t) in tags}
    wish.tags -= wish.avoid_tags
    return wish


def merge(lookup: Wish, model: Optional[Wish]) -> Wish:
    """The lookup's reading with the model's laid over it. Traits the lookup found keep its value;
    tags only the model found count together as one phrase, so they can't drown out what was typed.
    Small models like to add things to avoid, so the model's avoid list counts only when the request
    says no / not / without; hidden gems come from the lookup's words only."""
    if model is None:
        return lookup
    text = f" {(lookup.text or '').lower()} "
    if not any(n in text for n in NEGATORS):
        model = Wish(traits=model.traits, tags=model.tags)
    out = Wish(text=lookup.text, words_used=list(lookup.words_used), words_unknown=list(lookup.words_unknown),
               source="model+lookup")
    out.traits = {**model.traits, **lookup.traits}
    out.avoid_tags = lookup.avoid_tags | model.avoid_tags
    out.tags = (lookup.tags | model.tags) - out.avoid_tags
    out.tag_sets = [ts - out.avoid_tags for ts in lookup.tag_sets if ts - out.avoid_tags]
    extra = model.tags - lookup.tags - out.avoid_tags
    if extra:
        out.tag_sets.append(extra)
    out.hidden_gems = lookup.hidden_gems or model.hidden_gems
    return out


class LLMWishReader:
    """Reads wishes with a model, remembering the last `cache_size` answers (paging and repeats are instant).

    wait_seconds: how long a search waits for the model. A slower answer is still collected in the
    background and remembered, so that search answers with the word lookup now and with the model's
    reading next time (Show more, the same words again). None = always wait.
    avoid_groups: the vocabulary groups the model may put in "a" (things ruled out); small models
    like to rule out moods nobody mentioned, so this is best kept to content warnings."""

    def __init__(self, complete: Complete, noun: str = "items", cache_size: int = 256,
                 wait_seconds: Optional[float] = None, avoid_groups: Optional[List[str]] = None):
        self.complete = complete
        self.noun = noun
        self.cache_size = cache_size
        self.wait_seconds = wait_seconds
        self.avoid_groups = avoid_groups
        self._cache: "OrderedDict[str, Optional[Wish]]" = OrderedDict()
        self._pending: Dict[str, threading.Thread] = {}
        self._lock = threading.Lock()

    def _ask(self, key: str, text: str, vocab: Vocabulary) -> None:
        try:
            raw = self.complete(wish_prompt(text), _schema(vocab, self.avoid_groups), wish_system(vocab, self.noun))
            model = parse_answer(raw, vocab)
            if model is not None and self.avoid_groups is not None:
                allowed = {t for g in self.avoid_groups for t in vocab.tags.get(g, [])}
                model.avoid_tags &= allowed
            if model is not None:            # a failure is retried next time, not remembered
                with self._lock:
                    self._cache[key] = model
                    while len(self._cache) > self.cache_size:
                        self._cache.popitem(last=False)
        finally:
            with self._lock:
                self._pending.pop(key, None)

    def read(self, text: str, vocab: Vocabulary, lookup: Wish) -> Wish:
        key = " ".join((text or "").lower().split())
        if not key:
            return lookup
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return merge(lookup, self._cache[key])
            worker = self._pending.get(key)
            if worker is None:
                worker = threading.Thread(target=self._ask, args=(key, text, vocab), daemon=True,
                                          name="wishmatch-wish-reader")
                self._pending[key] = worker
                worker.start()
        worker.join(self.wait_seconds)
        with self._lock:
            model = self._cache.get(key)
        return merge(lookup, model)

    def cached(self, text: str) -> Optional[Wish]:
        return self._cache.get(" ".join((text or "").lower().split()))


def question_prompt(about: str, options: List[str], noun: str = "items") -> str:
    return (f"Someone is choosing {noun}. Write one short, friendly question that asks which they want "
            f"({about}), and a short answer (2-5 words) for each option, in the same order: "
            f"{json.dumps(options)}. Answer in JSON as {{\"question\": ..., \"options\": [...]}}.")


def word_question(about: str, options: List[str], complete: Optional[Complete] = None,
                  noun: str = "items") -> Dict[str, Any]:
    """A plain-English question for a choice between `options` (vocabulary words). Without a
    model, or when its answer doesn't fit, a fixed sentence and the words themselves."""
    fallback = {"question": f"More {about}?" if len(options) < 2 else f"Which feels right: {', '.join(options[:-1])} "
                f"or {options[-1]}?", "options": list(options), "source": "template"}
    if complete is None or not options:
        return fallback
    schema = {"type": "object", "properties": {"question": {"type": "string"},
                                               "options": {"type": "array", "items": {"type": "string"}}},
              "required": ["question", "options"]}
    raw = complete(question_prompt(about, options, noun), schema, "Answer only JSON.")
    try:
        data = json.loads(raw or "")
        q, opts = str(data["question"]).strip(), [str(o).strip() for o in data["options"]]
    except (ValueError, KeyError, TypeError):
        return fallback
    if not q or len(opts) != len(options) or not all(opts):
        return fallback
    return {"question": q, "options": opts, "source": "model"}
