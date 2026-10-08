#!/usr/bin/env python3
"""
Build the vibe index (training/build_vibe_index.py)  — run on the Mac

Design doc §2.2–2.5, Phase 3. Free (local compute only), ~10–20 min on an
Apple Silicon Mac for ~50k titles.

    uv run --with torch --with sentence-transformers --with onnx --with onnxscript \
        python training/build_vibe_index.py

Steps:
  1. Vibe document per title (services/vibe_document.py) → bge-small-en-v1.5
     → models/vibe/item_vectors.npy (float16) + item_ids.json
  2. Facets: the 14 dials (services/vibe_vocab.py) per title, 0–1. The LLM
     mood tag wins when it exists; otherwise the anchor projection
     sim(film, high sentence) − sim(film, low sentence), mapped to its
     percentile across the catalogue. → media_items.facets (JSON)
  3. Clusters: spherical k-means (k ≈ √(n/4), clamped to 80–150), named by
     the keywords / feels / genres most typical of each cluster
     → vibe_clusters + media_items.vibe_cluster_id
  4. Query encoder for the Pi: the same model as ONNX, INT8-quantised when it
     stays within 0.97 cosine of the full model, + tokenizer.json

Re-run after the TMDB backfill or the mood tagger adds data. `--skip-embed`
recomputes facets and clusters from the existing vectors (seconds), e.g.
after new mood tags. Copy models/vibe/ to the Pi and restart the server.

DATABASE_PATH overrides the catalogue (default: catalogue_full.db).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from database import DatabaseManager  # noqa: E402
from services.vibe_document import iter_documents  # noqa: E402
from services.vibe_vocab import DIALS, dial_anchor_pairs  # noqa: E402

MODEL_NAME = "BAAI/bge-small-en-v1.5"
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "
OUT_DIR = os.path.join(ROOT, "models", "vibe")
EMBED_BATCH = 64
MAX_QUERY_TOKENS = 64
INT8_GATE = 0.97
GATE_QUERIES = [
    "something chill after a late night", "a funny film we can half watch", "slow burn korean revenge",
    "cozy rainy day animation", "big dumb fun action", "a heavy drama that makes me cry",
    "like They Live but funnier", "mind-bending sci-fi that needs full attention",
    "wholesome feel-good family movie", "dark violent thriller", "romantic comedy for date night",
    "weird surreal art film",
]
CLUSTER_ITERS = 30
NAME_TERMS = 3
SKIP_TERMS = {"based on novel or book", "based on comic", "duringcreditsstinger", "aftercreditsstinger",
              "woman director", "sequel", "remake", "biography", "based on true story", "drama", "comedy"}


# ---------------------------------------------------------------------------
# 1. Embeddings
# ---------------------------------------------------------------------------

def pick_device() -> str:
    import torch
    if torch.backends.mps.is_available():
        return "mps"
    return "cuda" if torch.cuda.is_available() else "cpu"


def load_model(name: str):
    from sentence_transformers import SentenceTransformer
    print(f"📦 Loading {name}")
    return SentenceTransformer(name, device=pick_device())


def embed(model, texts: List[str], show: bool = True) -> np.ndarray:
    return np.asarray(model.encode(texts, batch_size=EMBED_BATCH, normalize_embeddings=True,
                                   show_progress_bar=show, convert_to_numpy=True), dtype=np.float32)


def pooling_of(model) -> str:
    for module in model:
        mode = getattr(module, "get_pooling_mode_str", None)
        if mode:
            return "mean" if "mean" in mode() else "cls"
    return "cls"


# ---------------------------------------------------------------------------
# 2. Facets
# ---------------------------------------------------------------------------

def anchor_directions(model) -> np.ndarray:
    pairs = dial_anchor_pairs()
    low = embed(model, [pairs[d][0] for d in DIALS], show=False)
    high = embed(model, [pairs[d][1] for d in DIALS], show=False)
    return (high - low).astype(np.float32)


def facet_quantiles(raw: np.ndarray) -> Dict[str, List[float]]:
    grid = np.linspace(0, 1, 101)
    out = {}
    for j, name in enumerate(DIALS):
        q = np.quantile(raw[:, j], grid)
        q = np.maximum.accumulate(q + np.arange(101) * 1e-9)   # strictly increasing for interp
        out[name] = [round(float(v), 6) for v in q]
    return out


def compute_facets(vectors: np.ndarray, anchors: np.ndarray, quants: Dict[str, List[float]],
                   llm_dials: List[Optional[Dict[str, float]]]) -> List[Dict[str, Any]]:
    raw = vectors @ anchors.T
    grid = np.linspace(0, 1, 101)
    cols = {name: np.interp(raw[:, j], quants[name], grid) for j, name in enumerate(DIALS)}
    facets = []
    for i, llm in enumerate(llm_dials):
        row: Dict[str, Any] = {}
        n_llm = 0
        for name in DIALS:
            if llm and isinstance(llm.get(name), (int, float)):
                row[name] = round(float(llm[name]), 3)
                n_llm += 1
            else:
                row[name] = round(float(cols[name][i]), 3)
        row["source"] = "llm" if n_llm == len(DIALS) else ("anchor" if n_llm == 0 else "mixed")
        facets.append(row)
    return facets


# ---------------------------------------------------------------------------
# 3. Clusters
# ---------------------------------------------------------------------------

def choose_k(n: int) -> int:
    return int(min(150, max(80, round(math.sqrt(n / 4))))) if n >= 400 else max(2, n // 5)


def spherical_kmeans(x: np.ndarray, k: int, iters: int = CLUSTER_ITERS, seed: int = 7):
    """k-means on the unit sphere (cosine), k-means++ seeding on a sample."""
    rng = np.random.default_rng(seed)
    n = len(x)
    sample = x[rng.choice(n, size=min(n, 20000), replace=False)]
    centroids = [sample[rng.integers(len(sample))]]
    dist = 1 - sample @ centroids[0]
    for _ in range(1, k):
        p = np.clip(dist, 0, None) ** 2
        p = p / p.sum() if p.sum() > 0 else None
        c = sample[rng.choice(len(sample), p=p)]
        centroids.append(c)
        dist = np.minimum(dist, 1 - sample @ c)
    c = np.stack(centroids)
    labels = np.zeros(n, dtype=np.int64)
    for it in range(iters):
        new = np.concatenate([np.argmax(x[s:s + 8192] @ c.T, axis=1) for s in range(0, n, 8192)])
        changed = int((new != labels).sum())
        labels = new
        for j in range(k):
            members = x[labels == j]
            if len(members):
                m = members.sum(axis=0)
                c[j] = m / (np.linalg.norm(m) or 1)
            else:                                   # re-seed an empty cluster on a far point
                c[j] = x[rng.integers(n)]
        if it and changed < max(1, n // 1000):
            break
    return labels, c.astype(np.float32)


def item_terms(row: Dict[str, Any]) -> List[str]:
    terms = set()
    for k in (row.get("tier2_keywords") or "").split(","):
        k = k.strip().lower()
        if k and k not in SKIP_TERMS:
            terms.add(k)
    for g in (row.get("genres") or "").split(","):
        g = g.strip().lower()
        if g and g not in SKIP_TERMS:
            terms.add(g)
    tags = row.get("vibe_tags")
    if tags:
        try:
            terms.update(json.loads(tags).get("feels") or [])
        except ValueError:
            pass
    return sorted(terms)


def name_clusters(labels: np.ndarray, k: int, terms: List[List[str]]) -> List[Dict[str, Any]]:
    n = len(terms)
    df = Counter(t for ts in terms for t in ts)
    out = []
    for j in range(k):
        members = np.flatnonzero(labels == j)
        size = len(members)
        counts = Counter(t for i in members for t in terms[i])
        floor = max(2, int(0.06 * size))
        scored = sorted(((c / size) * math.log(n / df[t]), t) for t, c in counts.items() if c >= floor)
        top = [t for _, t in reversed(scored)][:8]
        out.append({"size": size, "top": top, "label": ", ".join(top[:NAME_TERMS]) or f"cluster {j + 1}"})
    return out


def write_clusters(conn: sqlite3.Connection, ids: List[int], labels: np.ndarray, info: List[Dict[str, Any]],
                   vectors: np.ndarray, centroids: np.ndarray, votes: Dict[int, int]) -> None:
    conn.execute("DELETE FROM vibe_clusters")
    for j, c in enumerate(info):
        members = np.flatnonzero(labels == j)
        # Examples: close to the centre and well known, so the name can be checked at a glance.
        score = (vectors[members] @ centroids[j]) + 0.02 * np.log1p([votes.get(ids[i], 0) for i in members])
        examples = [ids[members[i]] for i in np.argsort(-score)[:6]]
        conn.execute("INSERT INTO vibe_clusters (id, label, size, top_keywords, example_ids) VALUES (?, ?, ?, ?, ?)",
                     (j + 1, c["label"], c["size"], ", ".join(c["top"]), json.dumps(examples)))
    conn.execute("UPDATE media_items SET vibe_cluster_id = NULL")
    conn.executemany("UPDATE media_items SET vibe_cluster_id = ? WHERE id = ?",
                     [(int(labels[i]) + 1, ids[i]) for i in range(len(ids))])


# ---------------------------------------------------------------------------
# 4. Query encoder (ONNX)
# ---------------------------------------------------------------------------

def export_query_encoder(model, out_dir: str, pooling: str) -> Dict[str, Any]:
    import torch
    from onnxruntime.quantization import QuantType, quantize_dynamic

    transformer = model[0]
    tokenizer = transformer.tokenizer
    encoder = transformer.auto_model.cpu().eval()
    tok_dir = os.path.join(out_dir, "tokenizer")
    tokenizer.save_pretrained(tok_dir)
    if not os.path.exists(os.path.join(tok_dir, "tokenizer.json")):
        raise RuntimeError("The model has no fast tokenizer (tokenizer.json); the Pi needs one.")

    enc = tokenizer([QUERY_PREFIX + "a calm film"], return_tensors="pt")
    names = ["input_ids", "attention_mask"] + (["token_type_ids"] if "token_type_ids" in enc else [])
    args = tuple(enc[n] for n in names)
    fp32 = os.path.join(out_dir, "query_encoder_fp32.onnx")
    int8 = os.path.join(out_dir, "query_encoder_int8.onnx")
    axes = {n: {0: "batch", 1: "seq"} for n in names}
    axes["last_hidden_state"] = {0: "batch", 1: "seq"}
    class Hidden(torch.nn.Module):
        # Keyword call: newer transformers' forward() takes more positional args.
        def __init__(self, enc):
            super().__init__()
            self.enc = enc

        def forward(self, *inputs):
            return self.enc(**dict(zip(names, inputs))).last_hidden_state

    with torch.no_grad():
        torch.onnx.export(Hidden(encoder), args, fp32, input_names=names, output_names=["last_hidden_state"],
                          dynamic_axes=axes, opset_version=17, dynamo=False)
    quantize_dynamic(fp32, int8, weight_type=QuantType.QInt8)

    reference = embed(model, [QUERY_PREFIX + q for q in GATE_QUERIES], show=False)
    scores = {p: _gate(p, tokenizer, names, pooling, reference) for p in (fp32, int8)}
    chosen = int8 if scores[int8] >= INT8_GATE else fp32
    final = os.path.join(out_dir, "query_encoder.onnx")
    os.replace(chosen, final)
    for p in (fp32, int8):
        if os.path.exists(p):
            os.remove(p)
    print(f"🔧 Query encoder: {'INT8' if chosen == int8 else 'fp32'} "
          f"(min cosine vs full model: int8 {scores[int8]:.4f}, fp32 {scores[fp32]:.4f})")
    return {"encoder": "int8" if chosen == int8 else "fp32", "gate_min_cosine": round(scores[int8], 4)}


def _gate(path: str, tokenizer, names: List[str], pooling: str, reference: np.ndarray) -> float:
    import onnxruntime as ort
    sess = ort.InferenceSession(path, providers=["CPUExecutionProvider"])
    worst = 1.0
    for q, ref in zip(GATE_QUERIES, reference):
        enc = tokenizer([QUERY_PREFIX + q], return_tensors="np", truncation=True, max_length=MAX_QUERY_TOKENS)
        hidden = sess.run(None, {n: enc[n].astype(np.int64) for n in names})[0]
        vec = hidden[0, 0] if pooling == "cls" else (hidden[0] * enc["attention_mask"][0][:, None]).sum(0)
        vec = vec / np.linalg.norm(vec)
        worst = min(worst, float(vec @ ref))
    return worst


# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description="Build the vibe index (embeddings, facets, clusters, query encoder)")
    ap.add_argument("--model", default=MODEL_NAME)
    ap.add_argument("--k", type=int, default=0, help="number of clusters (default √(n/4), 80–150)")
    ap.add_argument("--skip-embed", action="store_true", help="reuse models/vibe/item_vectors.npy")
    ap.add_argument("--skip-export", action="store_true", help="don't rebuild the ONNX query encoder")
    ap.add_argument("--out", default=OUT_DIR)
    args = ap.parse_args()

    db_path = os.getenv("DATABASE_PATH") or os.path.join(ROOT, "catalogue_full.db")
    DatabaseManager(db_path)                         # migrations (v6 facets, v7 vibe_clusters)
    conn = sqlite3.connect(db_path)
    os.makedirs(args.out, exist_ok=True)
    started = time.time()

    model = None
    if args.skip_embed:
        vectors = np.load(os.path.join(args.out, "item_vectors.npy")).astype(np.float32)
        with open(os.path.join(args.out, "item_ids.json")) as f:
            ids = json.load(f)
        print(f"♻️  Reusing {len(ids)} vectors")
    else:
        docs = list(iter_documents(conn))
        if not docs:
            sys.exit("❌ No titles in the catalogue.")
        ids = [i for i, _ in docs]
        print(f"📊 {len(docs)} vibe documents from {db_path}")
        model = load_model(args.model)
        vectors = embed(model, [d for _, d in docs])
        np.save(os.path.join(args.out, "item_vectors.npy"), vectors.astype(np.float16))
        with open(os.path.join(args.out, "item_ids.json"), "w") as f:
            json.dump(ids, f)
        print(f"✅ Embedded in {time.time() - started:.0f}s")

    meta = {r[0]: r[1:] for r in conn.execute(
        "SELECT id, vibe_tags, tier2_keywords, genres, COALESCE(vote_count, 0) FROM media_items")}
    llm_dials = []
    for i in ids:
        tags = meta.get(i, (None,))[0]
        try:
            llm_dials.append((json.loads(tags) or {}).get("dials") if tags else None)
        except ValueError:
            llm_dials.append(None)

    if model is None:
        model = load_model(args.model)
    anchors = anchor_directions(model)
    np.save(os.path.join(args.out, "anchors.npy"), anchors)
    quants = facet_quantiles(vectors @ anchors.T)
    facets = compute_facets(vectors, anchors, quants, llm_dials)
    with conn:
        conn.executemany("UPDATE media_items SET facets = ? WHERE id = ?",
                         [(json.dumps(f), i) for f, i in zip(facets, ids)])
    sources = Counter(f["source"] for f in facets)
    print(f"🎛️  Facets written: {dict(sources)}")

    k = args.k or choose_k(len(ids))
    labels, centroids = spherical_kmeans(vectors, k)
    np.save(os.path.join(args.out, "centroids.npy"), centroids)
    terms = [item_terms({"tier2_keywords": meta.get(i, (None,) * 4)[1], "genres": meta.get(i, (None,) * 4)[2],
                         "vibe_tags": meta.get(i, (None,) * 4)[0]}) for i in ids]
    info = name_clusters(labels, k, terms)
    with conn:
        write_clusters(conn, ids, labels, info, vectors, centroids, {i: m[3] for i, m in meta.items()})
    print(f"🗂️  {k} clusters. Biggest: " + "; ".join(
        f"{c['label']} ({c['size']})" for c in sorted(info, key=lambda c: -c["size"])[:5]))

    config_path = os.path.join(args.out, "config.json")
    config: Dict[str, Any] = {}
    if os.path.exists(config_path):
        with open(config_path) as f:
            config = json.load(f)
    pooling = pooling_of(model)
    if not args.skip_export:
        config.update(export_query_encoder(model, args.out, pooling))
    config.update({
        "model": args.model, "dim": int(vectors.shape[1]), "query_prefix": QUERY_PREFIX, "pooling": pooling,
        "max_query_tokens": MAX_QUERY_TOKENS, "count": len(ids), "clusters": k, "dials": list(DIALS),
        "facet_quantiles": quants, "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "database": os.path.basename(db_path),
    })
    with open(config_path, "w") as f:
        json.dump(config, f, indent=1)
    size = sum(os.path.getsize(os.path.join(dp, fn)) for dp, _, fns in os.walk(args.out) for fn in fns)
    print(f"✅ Done in {time.time() - started:.0f}s. models/vibe/ is {size / 1e6:.0f} MB; "
          "copy it to the Pi and restart the server.")


if __name__ == "__main__":
    main()
