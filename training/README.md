# 🧠 Model Fine-Tuning Guide

This directory contains everything needed to fine-tune a semantic embedding model
for enhanced movie recommendations. The trained model can optionally be deployed
back into the main application for vector-based similarity matching.

> **Note:** The main application works perfectly without a trained model.
> This is an optional enhancement for advanced semantic search.

---

## Prerequisites

```bash
pip install sentence-transformers torch onnxruntime
```

- **Mac (Apple Silicon):** PyTorch will auto-detect MPS GPU acceleration.
- **Google Colab:** CUDA GPU is auto-detected.
- **CPU:** Works on any machine, just slower.

---

## Step 1: Prepare Training Data

Edit `movie_triplets.json` to add your own triplets. Each triplet has:
- **anchor:** A natural language description of what a user might search for
- **positive:** A movie that perfectly matches the anchor query
- **negative:** A movie that sounds similar but is actually a poor match

The included sample has 15 triplets covering different moods and genres.

---

## Step 2: Run Fine-Tuning

```bash
python train_mps.py
```

This will:
1. Auto-detect your GPU (MPS on Mac, CUDA on Linux/Colab, CPU fallback)
2. Load the base model `BAAI/bge-small-en-v1.5` (33M params, ~130MB)
3. Train for 4 epochs on your triplet data
4. Save the fine-tuned model to `./fine_tuned_embedder/`

Expected training time:
- **Mac M1/M2/M3:** ~2-5 minutes
- **Google Colab (T4):** ~1-3 minutes
- **CPU:** ~10-15 minutes

---

## Step 3: Export to ONNX

```bash
python export_onnx.py
```

This converts the fine-tuned PyTorch model into ONNX format optimized for
fast CPU inference on ARM64 (Raspberry Pi) or any other platform.

Output: `model.onnx` (~50MB)

---

## Step 4: Deploy to Your App

```bash
mkdir -p ../models
cp model.onnx ../models/model.onnx
```

The main application's recommender service will automatically detect
`models/model.onnx` and use it for enhanced semantic matching.
If the file is absent, the app falls back to the standard SQL/taste-scoring pipeline.

---

## Architecture

```
Base Model: BAAI/bge-small-en-v1.5 (33M parameters)
    │
    ▼ Fine-tune with MultipleNegativesRankingLoss
    │
Fine-Tuned Model: ./fine_tuned_embedder/
    │
    ▼ Export with ONNX Runtime
    │
ONNX Model: model.onnx (optimized for ARM64/CPU inference)
    │
    ▼ Copy to ../models/
    │
Main App: Automatic detection & loading via onnxruntime
```
