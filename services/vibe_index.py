"""
Vibe index, the serving side (services/vibe_index.py)

Loads what training/build_vibe_index.py writes to models/vibe/ and answers
"which films feel like this sentence?" on the Pi:

    item_vectors.npy   [n, 384] float16, L2-normalised vibe-document vectors
    item_ids.json      media_items.id for each row
    anchors.npy        [dials, 384] high-minus-low anchor direction per dial
    query_encoder.onnx bge-small (INT8 when it passed the accuracy gate)
    tokenizer/         tokenizer.json for the `tokenizers` package
    config.json        model, prefix, pooling, dial names, facet quantiles

Brute-force cosine over ~50k × 384 is a single matrix-vector product
(a few ms on a Pi 5). Everything is optional: with no files, or without
onnxruntime / tokenizers, `is_ready()` is False and the recommender
behaves exactly as before.
"""

from __future__ import annotations

import json
import os
import re
import threading
from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np

from services.paths import models_dir

DEFAULT_ROOT = os.path.join(models_dir(), "vibe")

# "something like Arrival but funnier": the words after "like" up to a clause
# break. Phrases that start with a filler word are ordinary English ("I'd like
# a film", "like something light"), not a title.
_LIKE = re.compile(r"\blike\s+([^,.;!?]+?)(?=\s+(?:but|and|or|with|except|only)\b|[,.;!?]|$)", re.I)
_LIKE_FILLERS = {"a", "an", "to", "something", "some", "it", "that", "this", "one", "watching",
                 "seeing", "movies", "movie", "films", "film", "shows", "show", "anything", "stuff", "me", "us"}
LIKE_MIN_VOTES = 50
LIKE_BLEND = 0.5


class VibeIndex:
    def __init__(self, root: str = DEFAULT_ROOT):
        self.root = root
        self._lock = threading.Lock()
        self._loaded = False
        self._error: Optional[str] = None
        self.vectors: Optional[np.ndarray] = None
        self.ids: List[int] = []
        self.row: Dict[int, int] = {}
        self.config: Dict[str, Any] = {}
        self.anchors: Optional[np.ndarray] = None
        self._session = None
        self._tokenizer = None

    # -- loading -----------------------------------------------------------
    def files_present(self) -> bool:
        need = ("item_vectors.npy", "item_ids.json", "config.json", "query_encoder.onnx")
        return all(os.path.exists(os.path.join(self.root, f)) for f in need)

    def _load(self) -> bool:
        if self._loaded or self._error:
            return self._loaded
        with self._lock:
            if self._loaded or self._error:
                return self._loaded
            try:
                import onnxruntime as ort
                from tokenizers import Tokenizer

                with open(os.path.join(self.root, "config.json")) as f:
                    self.config = json.load(f)
                with open(os.path.join(self.root, "item_ids.json")) as f:
                    self.ids = [int(i) for i in json.load(f)]
                vectors = np.load(os.path.join(self.root, "item_vectors.npy")).astype(np.float32)
                if len(vectors) != len(self.ids):
                    raise ValueError(f"{len(vectors)} vectors for {len(self.ids)} ids")
                self.vectors = vectors
                self.row = {item_id: i for i, item_id in enumerate(self.ids)}
                anchors = os.path.join(self.root, "anchors.npy")
                self.anchors = np.load(anchors).astype(np.float32) if os.path.exists(anchors) else None

                tok = Tokenizer.from_file(os.path.join(self.root, "tokenizer", "tokenizer.json"))
                tok.enable_truncation(max_length=int(self.config.get("max_query_tokens", 64)))
                tok.no_padding()
                self._tokenizer = tok

                opts = ort.SessionOptions()
                opts.intra_op_num_threads = 2
                self._session = ort.InferenceSession(os.path.join(self.root, "query_encoder.onnx"), opts,
                                                     providers=["CPUExecutionProvider"])
                self._input_names = {i.name for i in self._session.get_inputs()}
                self._loaded = True
                print(f"[Vibe] Index loaded: {len(self.ids)} titles, model {self.config.get('model')}", flush=True)
            except Exception as e:  # missing package, files or a corrupt build
                self._error = str(e)
                print(f"[Vibe] Index not available ({e}); using the older ranking.", flush=True)
        return self._loaded

    def is_ready(self) -> bool:
        return self.files_present() and self._load()

    def status(self) -> Dict[str, Any]:
        return {"ready": self.is_ready(), "titles": len(self.ids), "error": self._error,
                "built_at": self.config.get("built_at"), "model": self.config.get("model")}

    # -- encoding ----------------------------------------------------------
    def encode_query(self, text: str) -> Optional[np.ndarray]:
        if not text or not text.strip() or not self.is_ready():
            return None
        enc = self._tokenizer.encode(self.config.get("query_prefix", "") + text.strip())
        ids = np.array([enc.ids], dtype=np.int64)
        mask = np.array([enc.attention_mask], dtype=np.int64)
        feeds = {"input_ids": ids, "attention_mask": mask}
        if "token_type_ids" in self._input_names:
            feeds["token_type_ids"] = np.zeros_like(ids)
        hidden = self._session.run(None, feeds)[0]           # [1, seq, dim]
        if self.config.get("pooling", "cls") == "mean":
            vec = (hidden * mask[:, :, None]).sum(axis=1)[0] / max(mask.sum(), 1)
        else:
            vec = hidden[0, 0]
        return _unit(vec)

    def item_vector(self, item_id: int) -> Optional[np.ndarray]:
        i = self.row.get(int(item_id))
        return None if i is None or self.vectors is None else self.vectors[i]

    def query_vector(self, text: str, like_ids: Iterable[int] = ()) -> Optional[np.ndarray]:
        """Text vector, pulled halfway towards each film the text names with "like"."""
        vec = self.encode_query(text)
        if vec is None:
            return None
        liked = [v for v in (self.item_vector(i) for i in like_ids) if v is not None]
        if liked:
            vec = _unit((1 - LIKE_BLEND) * vec + LIKE_BLEND * _unit(np.mean(liked, axis=0)))
        return vec

    # -- search ------------------------------------------------------------
    def similarities(self, vec: np.ndarray, item_ids: Iterable[int]) -> Dict[int, float]:
        """Cosine for each id that has a vector (titles added since the last
        build have none and are simply absent)."""
        rows = [(i, self.row[i]) for i in item_ids if i in self.row]
        if not rows or self.vectors is None:
            return {}
        sims = self.vectors[[r for _, r in rows]] @ vec
        return {i: float(s) for (i, _), s in zip(rows, sims)}

    def top(self, vec: np.ndarray, k: int = 500, exclude: Iterable[int] = ()) -> List[Tuple[int, float]]:
        if self.vectors is None:
            return []
        sims = self.vectors @ vec
        k = min(k + len(set(exclude)), len(sims))
        idx = np.argpartition(-sims, k - 1)[:k] if k < len(sims) else np.arange(len(sims))
        skip = set(exclude)
        ranked = sorted(((self.ids[i], float(sims[i])) for i in idx), key=lambda t: -t[1])
        return [(i, s) for i, s in ranked if i not in skip]

    def facets_for(self, vec: np.ndarray) -> Dict[str, float]:
        """The same 0-1 dial projection the films got, applied to any vector
        (e.g. "after a late night" lands low on energy and attention)."""
        if self.anchors is None:
            return {}
        names = self.config.get("dials") or []
        quants = self.config.get("facet_quantiles") or {}
        raw = self.anchors @ vec
        grid = np.linspace(0, 1, 101)
        out = {}
        for name, value in zip(names, raw):
            q = quants.get(name)
            if q:
                out[name] = round(float(np.interp(value, q, grid)), 3)
        return out


def _unit(vec: np.ndarray) -> np.ndarray:
    vec = np.asarray(vec, dtype=np.float32)
    n = float(np.linalg.norm(vec))
    return vec / n if n > 0 else vec


def like_phrases(query: Optional[str]) -> List[str]:
    """Candidate titles after "like" ("something like Arrival but funnier" -> ["Arrival"])."""
    out = []
    for m in _LIKE.finditer(query or ""):
        phrase = m.group(1).strip()
        words = phrase.split()
        if not words or words[0].lower() in _LIKE_FILLERS or len(words) > 6 or len(phrase) < 3:
            continue
        out.append(phrase)
    return out


def resolve_like_titles(query: Optional[str], db: Any, search: Any) -> List[Dict[str, Any]]:
    """Films named with "like", matched exactly or by title start, at least
    LIKE_MIN_VOTES votes so a stray word doesn't pull in an obscure title."""
    from services.catalogue_search import match_rank, normalise

    found = []
    for phrase in like_phrases(query):
        q = normalise(phrase)
        for item_id in search.search_ids(phrase, "all", limit=5):
            with db.get_connection() as conn:
                row = conn.execute("SELECT id, title, COALESCE(vote_count, 0) FROM media_items WHERE id = ?",
                                   (item_id,)).fetchone()
            if not row or row[2] < LIKE_MIN_VOTES:
                continue
            rank = match_rank(q, normalise(row[1]))
            if rank is not None and rank <= 1:
                found.append({"id": row[0], "title": row[1], "phrase": phrase})
                break
    return found
