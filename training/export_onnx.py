#!/usr/bin/env python3
"""
Converts the fine-tuned Sentence Transformer model to ONNX format
optimized for fast CPU inference on ARM64 (Raspberry Pi) or any platform.

Usage:
    python export_onnx.py
"""

import os
import sys

import numpy as np
import torch
from sentence_transformers import SentenceTransformer


def main():
    print("\n" + "=" * 60)
    print("  📦 ONNX Model Exporter")
    print("=" * 60 + "\n")

    model_dir = os.path.join(os.path.dirname(__file__), "fine_tuned_embedder")
    output_path = os.path.join(os.path.dirname(__file__), "model.onnx")

    if not os.path.exists(model_dir):
        print(f"❌ Fine-tuned model not found at: {model_dir}")
        print("   Run 'python train_mps.py' first to create the model.")
        sys.exit(1)

    # Load the fine-tuned model
    print(f"📦 Loading fine-tuned model from: {model_dir}")
    model = SentenceTransformer(model_dir)

    # Get the transformer module (first module in the pipeline)
    transformer = model[0]
    tokenizer = transformer.tokenizer
    encoder = transformer.auto_model

    # Move to CPU for export
    encoder = encoder.cpu()
    encoder.eval()

    # Create dummy input for tracing
    dummy_text = "A mind-bending science fiction movie about dreams"
    encoded = tokenizer(
        dummy_text,
        padding="max_length",
        truncation=True,
        max_length=128,
        return_tensors="pt",
    )

    input_ids = encoded["input_ids"]
    attention_mask = encoded["attention_mask"]
    token_type_ids = encoded.get("token_type_ids", torch.zeros_like(input_ids))

    # Export to ONNX
    print(f"\n🔧 Exporting to ONNX: {output_path}")

    torch.onnx.export(
        encoder,
        (input_ids, attention_mask, token_type_ids),
        output_path,
        input_names=["input_ids", "attention_mask", "token_type_ids"],
        output_names=["last_hidden_state"],
        dynamic_axes={
            "input_ids": {0: "batch_size", 1: "sequence"},
            "attention_mask": {0: "batch_size", 1: "sequence"},
            "token_type_ids": {0: "batch_size", 1: "sequence"},
            "last_hidden_state": {0: "batch_size", 1: "sequence"},
        },
        opset_version=14,
        do_constant_folding=True,
    )

    # Verify the exported model
    file_size_mb = os.path.getsize(output_path) / (1024 * 1024)
    print(f"\n✅ ONNX model exported successfully!")
    print(f"   File: {output_path}")
    print(f"   Size: {file_size_mb:.1f} MB")

    # Quick validation with onnxruntime
    try:
        import onnxruntime as ort

        session = ort.InferenceSession(output_path)
        ort_inputs = {
            "input_ids": input_ids.numpy(),
            "attention_mask": attention_mask.numpy(),
            "token_type_ids": token_type_ids.numpy(),
        }
        ort_output = session.run(None, ort_inputs)
        print(f"   Validation: ✅ ONNX output shape: {ort_output[0].shape}")
    except ImportError:
        print("   ⚠️  onnxruntime not installed — skipping validation")

    print(f"\nNext step: Copy model.onnx to ../models/ for deployment.")
    print(f"   mkdir -p ../models && cp model.onnx ../models/model.onnx")


if __name__ == "__main__":
    main()
