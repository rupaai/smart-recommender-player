#!/usr/bin/env python3
"""
TIGER Stage C — Semantic ID Sequence Predictor (training/train_tiger_predictor.py)

Causal Transformer over semantic ID triplets. Per the blueprint, raw item-ID
embeddings are replaced by a codebook embedding table (3 x 256 = 768 vectors):

    Input:   [[c1_1, c1_2, c1_3], [c2_1, c2_2, c2_3], ...]
    Target:  next item's [c3_1, c3_2, c3_3]
    Loss:    cross-entropy over the 256-way codebook dictionary per level

Output (training/tiger/):
    predictor.pt      trained weights
    config.json       hyper-parameters needed by the ONNX runtime engine

Usage:
    python train_tiger_predictor.py [--epochs 8] [--d-model 128]
"""

import argparse
import json
import os
import random
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import torch  # noqa: E402
import torch.nn as nn  # noqa: E402

NUM_LEVELS = 3
CODEBOOK_SIZE = 256


def detect_device() -> str:
    if torch.cuda.is_available():
        return "cuda"
    if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


class TIGERPredictor(nn.Module):
    """Causal Transformer producing per-level codebook logits per position."""

    def __init__(self, d_model: int = 128, nhead: int = 4, num_layers: int = 2,
                 max_seq_len: int = 10, dropout: float = 0.1):
        super().__init__()
        self.max_seq_len = max_seq_len
        # 3x256 codebook embedding table (768 vectors total), shared across positions
        self.codebook_embedding = nn.Embedding(NUM_LEVELS * CODEBOOK_SIZE, d_model)
        self.pos_embedding = nn.Embedding(max_seq_len, d_model)
        layer = nn.TransformerEncoderLayer(
            d_model=d_model, nhead=nhead, dim_feedforward=d_model * 4,
            dropout=dropout, batch_first=True, norm_first=True,
        )
        self.encoder = nn.TransformerEncoder(layer, num_layers=num_layers)
        self.layer_norm = nn.LayerNorm(d_model)
        # 3 independent 256-way heads (one per codebook level)
        self.heads = nn.ModuleList([nn.Linear(d_model, CODEBOOK_SIZE) for _ in range(NUM_LEVELS)])

    def forward(self, tokens: torch.Tensor, mask: torch.Tensor):
        """tokens: [B, L, 3] int64, mask: [B, L] float32.

        Returns logits [B, L, 3, 256].
        """
        b, l, _ = tokens.shape
        flat = tokens.reshape(b, l, NUM_LEVELS)  # already [B, L, 3]
        # Global token id per (level, code) pair into the 768-row table
        global_ids = flat + torch.tensor(
            [0, CODEBOOK_SIZE, 2 * CODEBOOK_SIZE], device=tokens.device
        ).view(1, 1, NUM_LEVELS)
        emb = self.codebook_embedding(global_ids).sum(dim=2)  # [B, L, D]
        positions = torch.arange(l, device=tokens.device).view(1, l)
        emb = emb + self.pos_embedding(positions)
        # Causal mask so position t only attends to <= t
        causal = torch.triu(
            torch.ones(l, l, dtype=torch.bool, device=tokens.device), diagonal=1
        )
        h = self.encoder(emb, mask=causal, src_key_padding_mask=(mask < 0.5))
        h = self.layer_norm(h)
        logits = torch.stack([head(h) for head in self.heads], dim=2)  # [B, L, 3, 256]
        return logits


def make_training_examples(sequences: list, max_seq_len: int):
    """Sliding windows -> (input_seq, target_tuple) pairs.

    Input:  sequence[:t]        (all tuples before position t)
    Target: sequence[t]         (next item's semantic ID triplet)
    """
    examples = []
    for seq in sequences:
        # Slide over the WHOLE sequence. The previous version first cut every
        # sequence down to its last (max_seq_len - 1) items, so a 60-film
        # history (or a 300-rating MovieLens user) produced only ~8 examples
        # and everything before them was thrown away.
        for t in range(1, len(seq)):
            examples.append((seq[max(0, t - (max_seq_len - 1)):t], seq[t]))
    return examples


def pad_batch(batch_seqs: list, max_seq_len: int, pad_code: int = 0):
    """Pads a list of [L_i, 3] sequences to [B, max_seq_len, 3] + mask."""
    b = len(batch_seqs)
    tokens = np.zeros((b, max_seq_len, NUM_LEVELS), dtype=np.int64)
    mask = np.zeros((b, max_seq_len), dtype=np.float32)
    for i, seq in enumerate(batch_seqs):
        seq = seq[-max_seq_len:]
        tokens[i, : len(seq)] = seq
        mask[i, : len(seq)] = 1.0
    return tokens, mask


def main() -> None:
    ap = argparse.ArgumentParser(description="TIGER Stage C: semantic ID sequence predictor")
    ap.add_argument("--epochs", type=int, default=8)
    ap.add_argument("--batch-size", type=int, default=64)
    ap.add_argument("--lr", type=float, default=3e-4)
    ap.add_argument("--d-model", type=int, default=128)
    ap.add_argument("--layers", type=int, default=2)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()
    random.seed(args.seed)
    torch.manual_seed(args.seed)

    here = os.path.dirname(os.path.abspath(__file__))
    out_dir = os.path.join(here, "tiger")
    seq_path = os.path.join(out_dir, "sequences.json")
    if not os.path.exists(seq_path):
        print("❌ sequences.json missing — run bootstrap_sequences.py first.")
        sys.exit(1)
    with open(seq_path, "r", encoding="utf-8") as f:
        sequences = json.load(f)
    print(f"📊 Sequences: {len(sequences)}")

    max_seq_len = 10
    examples = make_training_examples(sequences, max_seq_len)
    if not examples:
        print("❌ No training examples could be built (sequences too short).")
        sys.exit(1)
    print(f"📊 Training examples (sliding windows): {len(examples)}")

    device = detect_device()
    print(f"🖥  Device: {device}")
    model = TIGERPredictor(d_model=args.d_model, num_layers=args.layers, max_seq_len=max_seq_len).to(device)
    opt = torch.optim.AdamW(model.parameters(), lr=args.lr)
    loss_ce = nn.CrossEntropyLoss()

    rng = random.Random(args.seed)
    n_params = sum(p.numel() for p in model.parameters())
    print(f"🧠 Model parameters: {n_params:,}")
    print(f"\n🏋️  Training for {args.epochs} epochs ...")
    for epoch in range(1, args.epochs + 1):
        rng.shuffle(examples)
        total_loss, n_batches = 0.0, 0
        model.train()
        for start in range(0, len(examples), args.batch_size):
            batch = examples[start: start + args.batch_size]
            if len(batch) < 2:
                continue
            inputs = [ex[0] for ex in batch]
            targets = np.array([ex[1] for ex in batch], dtype=np.int64)  # [B, 3]
            tokens, mask = pad_batch(inputs, max_seq_len)
            tokens_t = torch.from_numpy(tokens).to(device)
            mask_t = torch.from_numpy(mask).to(device)
            tgt_t = torch.from_numpy(targets).to(device)

            logits = model(tokens_t, mask_t)  # [B, L, 3, 256]
            last_idx = int(mask_t.sum(dim=1).max().item()) - 1
            last_logits = logits[:, last_idx]  # [B, 3, 256]
            loss = sum(
                loss_ce(last_logits[:, level], tgt_t[:, level]) for level in range(NUM_LEVELS)
            ) / NUM_LEVELS

            opt.zero_grad()
            loss.backward()
            opt.step()
            total_loss += loss.item()
            n_batches += 1
        if n_batches:
            print(f"   epoch {epoch:2d}/{args.epochs}  loss={total_loss / n_batches:.4f}")

    torch.save(model.state_dict(), os.path.join(out_dir, "predictor.pt"))
    config = {
        "d_model": args.d_model,
        "num_layers": args.layers,
        "max_seq_len": max_seq_len,
        "num_levels": NUM_LEVELS,
        "codebook_size": CODEBOOK_SIZE,
        "seed": args.seed,
    }
    with open(os.path.join(out_dir, "config.json"), "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)

    print(f"\n✅ Stage C complete")
    print(f"   -> {os.path.join(out_dir, 'predictor.pt')}")
    print(f"   -> {os.path.join(out_dir, 'config.json')}")
    print("Next: python export_tiger_onnx.py")


if __name__ == "__main__":
    main()

