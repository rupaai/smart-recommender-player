#!/usr/bin/env python3
"""
TIGER — ONNX Export, INT8 Quantization & Prefix Trie (training/export_tiger_onnx.py)

1. Exports the trained predictor to ONNX (opset 14, dynamic batch).
2. Applies dynamic INT8 quantization via onnxruntime.quantization.
3. Verifies the deployment gates: file < 15 MB, single inference < 5 ms CPU.
4. Builds the static prefix trie (semantic ID tuple -> item IDs) so runtime
   lookups can never hallucinate an item.
5. End-to-end smoke test: sample sequence -> ONNX logits -> beam-decoded
   tuple -> trie -> concrete catalog item.

Output (training/tiger/):
    predictor.onnx, predictor_int8.onnx, prefix_trie.json

Usage:
    python export_tiger_onnx.py
"""

import argparse
import json
import os
import sys
import time
from collections import defaultdict

import numpy as np
import torch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from train_tiger_predictor import TIGERPredictor  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "services"))
from generative_retriever import (  # noqa: E402
    NUM_LEVELS,
    CODEBOOK_SIZE,
    load_prefix_trie,
    trie_lookup,
    beam_decode_top_tuples,
)

MAX_FILE_MB = 15.0
MAX_LATENCY_MS = 5.0


def export_onnx(model: TIGERPredictor, out_path: str) -> None:
    model.eval()
    tokens = torch.zeros((1, model.max_seq_len, NUM_LEVELS), dtype=torch.int64)
    mask = torch.zeros((1, model.max_seq_len), dtype=torch.float32)
    torch.onnx.export(
        model,
        (tokens, mask),
        out_path,
        input_names=["tokens", "mask"],
        output_names=["logits"],
        dynamic_axes={
            "tokens": {0: "batch"},
            "mask": {0: "batch"},
            "logits": {0: "batch"},
        },
        opset_version=14,
        do_constant_folding=True,
        # Legacy TorchScript tracer: the causal/padding masks trip up the
        # newer torch.export-based path with data-dependent shape guards.
        dynamo=False,
    )


def verify_gates(int8_path: str, max_seq_len: int) -> None:
    import onnxruntime as ort

    size_mb = os.path.getsize(int8_path) / (1024 * 1024)
    print(f"   INT8 file size: {size_mb:.2f} MB (gate: < {MAX_FILE_MB} MB)")
    if size_mb >= MAX_FILE_MB:
        raise RuntimeError(f"INT8 model exceeds {MAX_FILE_MB} MB")

    session = ort.InferenceSession(int8_path, providers=["CPUExecutionProvider"])
    tokens = np.random.randint(0, CODEBOOK_SIZE, (1, max_seq_len, NUM_LEVELS), dtype=np.int64)
    mask = np.ones((1, max_seq_len), dtype=np.float32)
    feeds = {session.get_inputs()[0].name: tokens, session.get_inputs()[1].name: mask}
    session.run(None, feeds)
    n_iters, elapsed = 20, 0.0
    for _ in range(n_iters):
        t0 = time.perf_counter()
        session.run(None, feeds)
        elapsed += (time.perf_counter() - t0) * 1000
    avg_ms = elapsed / n_iters
    print(f"   avg CPU latency: {avg_ms:.2f} ms (gate: < {MAX_LATENCY_MS} ms)")
    if avg_ms >= MAX_LATENCY_MS:
        raise RuntimeError(f"CPU latency {avg_ms:.2f} ms exceeds {MAX_LATENCY_MS} ms")


def main() -> None:
    ap = argparse.ArgumentParser(description="TIGER: ONNX export + INT8 + prefix trie")
    ap.add_argument("--beam-top-k", type=int, default=5, help="tuples to smoke-test")
    args = ap.parse_args()

    here = os.path.dirname(os.path.abspath(__file__))
    out_dir = os.path.join(here, "tiger")

    weights_path = os.path.join(out_dir, "predictor.pt")
    config_path = os.path.join(out_dir, "config.json")
    semantic_path = os.path.join(out_dir, "semantic_ids.json")
    for p in (weights_path, config_path, semantic_path):
        if not os.path.exists(p):
            print(f"❌ Missing {p} — run the earlier TIGER steps first.")
            sys.exit(1)

    with open(config_path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    model = TIGERPredictor(
        d_model=cfg["d_model"], num_layers=cfg["num_layers"], max_seq_len=cfg["max_seq_len"]
    )
    model.load_state_dict(torch.load(weights_path, map_location="cpu"))
    max_seq_len = int(cfg["max_seq_len"])
    print(f"📦 Loaded predictor ({sum(p.numel() for p in model.parameters()):,} params)")

    fp32_path = os.path.join(out_dir, "predictor.onnx")
    print(f"\n🔧 Exporting FP32 ONNX: {fp32_path}")
    export_onnx(model, fp32_path)

    from onnxruntime.quantization import quantize_dynamic, QuantType

    int8_path = os.path.join(out_dir, "predictor_int8.onnx")
    print(f"\n🔧 Dynamic INT8 quantization: {int8_path}")
    quantize_dynamic(fp32_path, int8_path, weight_type=QuantType.QInt8)

    print("\n🚦 Verifying deployment gates ...")
    verify_gates(int8_path, max_seq_len)

    # ---- Prefix trie -------------------------------------------------------
    print("\n🌳 Building prefix trie ...")
    with open(semantic_path, "r", encoding="utf-8") as f:
        semantic_doc = json.load(f)
    trie = load_prefix_trie(semantic_doc)

    def count_items(node) -> int:
        total = len(node.get("__items__", []))
        return total + sum(count_items(child) for k, child in node.items() if k != "__items__")

    print(f"   trie covers {count_items(trie)} items")
    with open(os.path.join(out_dir, "prefix_trie.json"), "w", encoding="utf-8") as f:
        json.dump(trie, f)

    # ---- End-to-end smoke test ---------------------------------------------
    print("\n🧪 End-to-end smoke test (ONNX -> beam decode -> trie) ...")
    import onnxruntime as ort

    session = ort.InferenceSession(int8_path, providers=["CPUExecutionProvider"])
    in0 = session.get_inputs()[0].name
    in1 = session.get_inputs()[1].name
    seq = np.random.randint(0, CODEBOOK_SIZE, (1, max_seq_len, NUM_LEVELS), dtype=np.int64)
    mask = np.ones((1, max_seq_len), dtype=np.float32)
    seq[0, 3:] = 0
    mask[0, 3:] = 0.0
    logits = session.run(None, {in0: seq, in1: mask})[0]
    top = beam_decode_top_tuples(logits[0, 2], top_k=args.beam_top_k)
    resolved = 0
    for codes, prob in top:
        items = trie_lookup(trie, codes)
        resolved += len(items)
        print(f"   tuple {list(codes)} p={prob:.3f} -> {len(items)} item(s)")
    print(f"   resolved {resolved} item(s) across {len(top)} tuples")

    print("\n✅ Export complete")
    for name in ("predictor.onnx", "predictor_int8.onnx", "prefix_trie.json"):
        p = os.path.join(out_dir, name)
        print(f"   {name}: {os.path.getsize(p) / (1024 * 1024):.2f} MB")
    print("\nDeploy:")
    print("   mkdir -p ../models/tiger")
    print("   cp training/tiger/{predictor_int8.onnx,semantic_ids.json,prefix_trie.json,item_map.json,config.json} ../models/tiger/")


if __name__ == "__main__":
    main()

