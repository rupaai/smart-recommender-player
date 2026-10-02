#!/usr/bin/env python3
"""
TIGER Stage B — RQ-VAE Semantic ID Quantizer (training/train_rqvae.py)

Compresses dense text embeddings into 3-level semantic ID tuples [c1, c2, c3]
(codes in 0..255) per the TIGER architecture (arXiv:2305.05065):

    Encoder:   Embedding_Dim -> 128 -> 64 (ReLU)
    Quantizer: 3 residual codebook levels, K=256, D=64
    Decoder:   64 -> 128 -> Embedding_Dim (ReLU)
    Loss:      MSE reconstruction + commitment loss

Collisions (two items with the same 3-tuple) are disambiguated by appending
a unique 4th leaf token to the colliding items' codes.

Output (training/tiger/):
    semantic_ids.json  {item_id: [c1, c2, c3(, leaf)]} + codebook stats

Usage:
    python train_rqvae.py [--epochs 4000]
"""

import argparse
import json
import os
import sys
from collections import Counter, defaultdict

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import torch  # noqa: E402
import torch.nn as nn  # noqa: E402

NUM_LEVELS = 3
CODEBOOK_SIZE = 256
CODE_DIM = 64
ENC_HIDDEN = 128
COMMITMENT_BETA = 0.25


def detect_device() -> str:
    if torch.cuda.is_available():
        return "cuda"
    if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


class RQVAE(nn.Module):
    """2-layer encoder/decoder + 3-level residual codebook quantizer."""

    def __init__(self, input_dim: int, hidden: int = ENC_HIDDEN, code_dim: int = CODE_DIM):
        super().__init__()
        self.encoder = nn.Sequential(
            nn.Linear(input_dim, hidden),
            nn.ReLU(),
            nn.Linear(hidden, code_dim),
        )
        self.decoder = nn.Sequential(
            nn.Linear(code_dim, hidden),
            nn.ReLU(),
            nn.Linear(hidden, input_dim),
        )
        self.codebooks = nn.ModuleList(
            [nn.Embedding(CODEBOOK_SIZE, code_dim) for _ in range(NUM_LEVELS)]
        )
        for cb in self.codebooks:
            nn.init.uniform_(cb.weight, -1.0 / CODEBOOK_SIZE, 1.0 / CODEBOOK_SIZE)

    def quantize(self, z: torch.Tensor):
        """Straight-through residual quantization.

        Returns (codes [L, B], z_q_st [B, D], z_q [B, D]).
        """
        residual = z
        z_q = torch.zeros_like(z)
        codes = []
        for codebook in self.codebooks:
            w = codebook.weight  # [K, D]
            dists = torch.cdist(residual.unsqueeze(0), w.unsqueeze(0)).squeeze(0)  # [B, K]
            idx = dists.argmin(dim=1)  # [B]
            codes.append(idx)
            q = w[idx]  # [B, D]
            z_q = z_q + q
            residual = residual - q.detach()
        # Straight-through estimator: gradients flow into the encoder
        z_q_st = z + (z_q - z).detach()
        return torch.stack(codes, dim=0), z_q_st, z_q

    def forward(self, x: torch.Tensor):
        z = self.encoder(x)
        codes, z_q_st, z_q = self.quantize(z)
        recon = self.decoder(z_q_st)
        return z, codes, z_q, recon


def main() -> None:
    ap = argparse.ArgumentParser(description="TIGER Stage B: RQ-VAE semantic ID quantizer")
    ap.add_argument("--epochs", type=int, default=4000)
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--batch-size", type=int, default=256)
    args = ap.parse_args()

    here = os.path.dirname(os.path.abspath(__file__))
    out_dir = os.path.join(here, "tiger")
    emb_path = os.path.join(out_dir, "embeddings.npy")
    map_path = os.path.join(out_dir, "item_map.json")
    if not os.path.exists(emb_path) or not os.path.exists(map_path):
        print("❌ Stage A output missing — run prepare_semantic_data.py first.")
        sys.exit(1)

    embeddings = np.load(emb_path).astype(np.float32)
    with open(map_path, "r", encoding="utf-8") as f:
        item_map = json.load(f)["items"]
    order = [int(item_map[str(i)]["item_id"]) for i in range(embeddings.shape[0])]
    n, dim = embeddings.shape
    print(f"📊 Embeddings: {n} items x {dim} dims")
    device = detect_device()
    print(f"🖥  Device: {device}")

    model = RQVAE(dim).to(device)
    opt = torch.optim.Adam(model.parameters(), lr=args.lr)
    x_all = torch.from_numpy(embeddings).to(device)

    print(f"\n🏋️  Training RQ-VAE for {args.epochs} steps ...")
    model.train()
    for step in range(1, args.epochs + 1):
        idx = torch.randint(0, n, (min(args.batch_size, n),), device=device)
        x = x_all[idx]
        z, codes, z_q, recon = model(x)
        recon_loss = torch.nn.functional.mse_loss(recon, x)
        # Commitment loss: ||z - sg(z_q)||^2
        commit_loss = torch.nn.functional.mse_loss(z, z_q.detach())
        loss = recon_loss + COMMITMENT_BETA * commit_loss
        opt.zero_grad()
        loss.backward()
        opt.step()
        if step % 500 == 0 or step == 1:
            print(f"   step {step:5d}  recon={recon_loss.item():.4f}  commit={commit_loss.item():.4f}")

    # ---- Quantize the full catalogue -------------------------------------
    model.eval()
    with torch.no_grad():
        _, codes_all, _, _ = model(x_all)
    codes_np = codes_all.cpu().numpy().astype(int)  # [L, N]
    tuples = codes_np.T.tolist()  # N x 3

    # ---- Collision handling: unique leaf token per colliding group --------
    counts = Counter(map(tuple, tuples))
    colliding = {t for t, c in counts.items() if c > 1}
    leaf_counters: dict = defaultdict(int)
    semantic_ids: dict = {}
    for item_id, t in zip(order, tuples):
        codes = list(t)
        if tuple(t) in colliding:
            codes.append(leaf_counters[tuple(t)])
            leaf_counters[tuple(t)] += 1
        semantic_ids[str(item_id)] = codes

    n_unique = len(set(map(tuple, tuples)))
    utilization = [
        round(len(set(codes_np[level].tolist())) / CODEBOOK_SIZE, 3) for level in range(NUM_LEVELS)
    ]

    out = {
        "num_levels": NUM_LEVELS,
        "codebook_size": CODEBOOK_SIZE,
        "code_dim": CODE_DIM,
        "items": semantic_ids,
        "stats": {
            "total_items": n,
            "unique_tuples_before_leaf": n_unique,
            "colliding_groups": len(colliding),
            "codebook_utilization": utilization,
        },
    }
    with open(os.path.join(out_dir, "semantic_ids.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)

    print(f"\n✅ Stage B complete")
    print(f"   unique 3-tuples: {n_unique}/{n}  |  colliding groups: {len(colliding)} (leaf tokens added)")
    print(f"   codebook utilization per level: {utilization}")
    print(f"   -> {os.path.join(out_dir, 'semantic_ids.json')}")
    print("Next: python bootstrap_sequences.py")


if __name__ == "__main__":
    main()

