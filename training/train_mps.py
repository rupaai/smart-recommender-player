#!/usr/bin/env python3
"""
Fine-tunes BAAI/bge-small-en-v1.5 on movie triplets.
Auto-detects Apple Silicon MPS, CUDA, or falls back to CPU.

Usage:
    python train_mps.py
"""

import json
import os
import sys

import torch
from sentence_transformers import SentenceTransformer, InputExample, losses
from torch.utils.data import DataLoader


def detect_device() -> str:
    """Auto-detect the best available compute device."""
    if torch.cuda.is_available():
        device = "cuda"
        print(f"✅ CUDA GPU detected: {torch.cuda.get_device_name(0)}")
    elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        device = "mps"
        print("✅ Apple Silicon MPS GPU detected")
    else:
        device = "cpu"
        print("ℹ️  No GPU detected — using CPU (slower but works fine)")
    return device


def load_triplets(path: str = "movie_triplets.json") -> list:
    """Loads triplet training data from JSON file."""
    triplets_path = os.path.join(os.path.dirname(__file__), path)
    if not os.path.exists(triplets_path):
        print(f"❌ Training data not found at: {triplets_path}")
        sys.exit(1)

    with open(triplets_path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    examples = []
    for t in raw:
        examples.append(
            InputExample(texts=[t["anchor"], t["positive"], t["negative"]])
        )

    print(f"📊 Loaded {len(examples)} training triplets")
    return examples


def main():
    print("\n" + "=" * 60)
    print("  🧠 Movie Embedding Model Fine-Tuner")
    print("=" * 60 + "\n")

    # 1. Detect device
    device = detect_device()

    # 2. Load base model
    base_model_name = "BAAI/bge-small-en-v1.5"
    print(f"\n📦 Loading base model: {base_model_name}")
    model = SentenceTransformer(base_model_name, device=device)
    print(f"   Model loaded ({model.get_sentence_embedding_dimension()}d embeddings)")

    # 3. Load training data
    examples = load_triplets()

    # 4. Create dataloader
    train_dataloader = DataLoader(examples, shuffle=True, batch_size=4)

    # 5. Define loss
    train_loss = losses.MultipleNegativesRankingLoss(model)

    # 6. Train
    num_epochs = 4
    output_path = os.path.join(os.path.dirname(__file__), "fine_tuned_embedder")

    print(f"\n🏋️ Training for {num_epochs} epochs...")
    print(f"   Batch size: 4")
    print(f"   Loss: MultipleNegativesRankingLoss")
    print(f"   Output: {output_path}\n")

    model.fit(
        train_objectives=[(train_dataloader, train_loss)],
        epochs=num_epochs,
        warmup_steps=int(len(train_dataloader) * 0.1),
        output_path=output_path,
        show_progress_bar=True,
    )

    print(f"\n✅ Fine-tuned model saved to: {output_path}")
    print("\nNext step: Run 'python export_onnx.py' to convert to ONNX format.")


if __name__ == "__main__":
    main()
