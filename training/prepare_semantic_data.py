#!/usr/bin/env python3
"""
TIGER Stage A — Catalog Text Embeddings (training/prepare_semantic_data.py)

Extracts every catalogue item into the canonical text format
    "Title: {title} | Genres: {genres} | Keywords: {keywords} | Overview: {overview}"
and encodes it with BAAI/bge-small-en-v1.5 (reusing the fine-tuned embedder
from train_mps.py when present, otherwise the base model).

Output (training/tiger/):
    embeddings.npy   [Total_Items, 384] float32 matrix
    item_map.json    {corpus_index -> {"item_id": int, "text": str}}

Usage:
    python prepare_semantic_data.py [--db ../catalogue_full.db]
"""

import argparse
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import DatabaseManager  # noqa: E402

MODEL_NAME = "BAAI/bge-small-en-v1.5"
EMBED_BATCH = 128


def build_catalog_text(item: dict) -> str:
    """Canonical catalog text string used for Stage A embeddings."""
    return (
        f"Title: {item.get('title') or ''} | "
        f"Genres: {item.get('genres') or ''} | "
        f"Keywords: {item.get('tier2_keywords') or ''} | "
        f"Overview: {(item.get('overview') or '').strip()}"
    )


def load_embedder(device: str):
    """Reuses the fine-tuned embedder when available, else the base model."""
    from sentence_transformers import SentenceTransformer

    fine_tuned = os.path.join(os.path.dirname(__file__), "fine_tuned_embedder")
    if os.path.isdir(fine_tuned):
        print(f"📦 Loading fine-tuned embedder: {fine_tuned}")
        return SentenceTransformer(fine_tuned, device=device)
    print(f"📦 Loading base model: {MODEL_NAME} (fine-tuned model not found — run train_mps.py to improve)")
    return SentenceTransformer(MODEL_NAME, device=device)


def main() -> None:
    ap = argparse.ArgumentParser(description="TIGER Stage A: catalog text embeddings")
    ap.add_argument("--db", default=None, help="SQLite catalogue path (defaults to DATABASE_PATH env or config)")
    args = ap.parse_args()

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out_dir = os.path.join(os.path.dirname(__file__), "tiger")
    os.makedirs(out_dir, exist_ok=True)

    db_path = args.db or os.environ.get("DATABASE_PATH") or "catalogue.db"
    if not os.path.isabs(db_path):
        db_path = os.path.join(root, db_path)
    db = DatabaseManager(db_path)

    # All items regardless of watch status — semantic IDs cover the whole catalog
    items = db.get_reference_titles()
    if not items:
        print("❌ Catalogue is empty — run the /api/sync or the full catalogue builder first.")
        sys.exit(1)
    print(f"📊 Catalogue items: {len(items)}")

    corpus = [build_catalog_text(it) for it in items]
    item_ids = [int(it["id"]) for it in items]

    device = "cpu"
    try:
        import torch

        if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            device = "mps"
        elif torch.cuda.is_available():
            device = "cuda"
    except ImportError:
        print("⚠️  torch not installed — sentence-transformers will fall back internally")
    print(f"🖥  Device: {device}")

    model = load_embedder(device)
    print(f"\n🧮 Encoding {len(corpus)} catalog texts ...")
    embeddings = model.encode(
        corpus,
        batch_size=EMBED_BATCH,
        show_progress_bar=True,
        convert_to_numpy=True,
        normalize_embeddings=True,
    ).astype(np.float32)

    item_map = {
        str(i): {"item_id": item_ids[i], "text": corpus[i]}
        for i in range(len(items))
    }

    np.save(os.path.join(out_dir, "embeddings.npy"), embeddings)
    with open(os.path.join(out_dir, "item_map.json"), "w", encoding="utf-8") as f:
        json.dump(
            {"model": MODEL_NAME, "embeddings_shape": list(embeddings.shape), "items": item_map},
            f,
            ensure_ascii=False,
        )

    print(f"\n✅ Stage A complete")
    print(f"   embeddings: {embeddings.shape} -> {os.path.join(out_dir, 'embeddings.npy')}")
    print(f"   item map:   {len(item_map)} entries -> {os.path.join(out_dir, 'item_map.json')}")
    print("Next: python train_rqvae.py")


if __name__ == "__main__":
    main()
