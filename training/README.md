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

---

# 🐯 TIGER Generative Retrieval Pipeline (Semantic IDs)

A second, parallel pipeline implementing **TIGER: Recommender Systems with
Generative Retrieval** ([arXiv:2305.05065](https://arxiv.org/abs/2305.05065)).
Catalog items are compressed into 3-level **Semantic IDs** `[c1, c2, c3]`
(codes 0–255) by an RQ-VAE, and a small causal Transformer predicts the next
item's Semantic ID tuple from watch history. At runtime the tuple is resolved
back to real items through a prefix trie — **0% hallucinated lookups**.

The existing embedding fine-tuner above is **Stage A of this pipeline**:
`prepare_semantic_data.py` automatically reuses `fine_tuned_embedder/` if you
have trained it, otherwise it falls back to the base model.

```text
[ Catalog Text ] ──> [ 1. Text Encoder ] ──> [ 2. RQ-VAE Quantizer ] ──> [ 3. Sequence Predictor ]
        (Step A: train_mps.py +          (Step C: train_rqvae.py)      (Step E: train_tiger_predictor.py)
         prepare_semantic_data.py)                                          │
                                                                            ▼
                                                                 [ ONNX INT8 Export ]  (Step F)
```

## Prerequisites (training machine only — NOT the Raspberry Pi)

```bash
uv pip install torch sentence-transformers onnx onnxruntime onnxscript
```

(`onnxscript` is required by torch ≥2.14's ONNX exporter.)

`onnxruntime` is also a runtime dependency of the main app (`pyproject.toml`);
the Pi needs it to load the INT8 model.

## Step A — Text embeddings

```bash
export DATABASE_PATH=catalogue_full.db   # or catalogue.db for a smoke test
python prepare_semantic_data.py
```

Output: `tiger/embeddings.npy` + `tiger/item_map.json`.

## Step B — (optional, recommended) Fine-tune the embedder

Existing pipeline — run `python train_mps.py` + `python export_onnx.py`
**before** Step A and the TIGER embeddings will use the fine-tuned model.

## Step C — RQ-VAE semantic IDs

```bash
python train_rqvae.py            # ~4000 steps, minutes on MPS
```

Output: `tiger/semantic_ids.json` (item_id → `[c1, c2, c3(, leaf)]`).
Collision groups get a unique 4th leaf token. Prints codebook utilization.

## Step D — Interaction sequences

```bash
# Real watch history (uses watch_history table):
python bootstrap_sequences.py

# Sparse local history? Map MovieLens to your catalog by (title, year):
#   download https://grouplens.org/datasets/movielens/ ml-latest-small.zip
python bootstrap_sequences.py --movielens ~/Downloads/ml-latest-small/ratings.csv
```

Output: `tiger/sequences.json`. With < 50 sequences, a synthetic genre
co-watch bootstrap fills the gap so training always works offline.

## Step E — Train the sequence predictor

```bash
python train_tiger_predictor.py   # defaults: 8 epochs, d_model=128, seq_len=10
```

Causal Transformer, 3×256 codebook embedding table (768 vectors), three
256-way cross-entropy heads. Output: `tiger/predictor.pt` + `tiger/config.json`.

## Step F — Export ONNX + INT8 + trie

```bash
python export_tiger_onnx.py
```

Verifies the deployment gates: **INT8 model < 15 MB** and **CPU latency
< 5 ms** (fails loudly otherwise), builds `tiger/prefix_trie.json`, and runs
an end-to-end smoke test (sequence → ONNX → beam decode → trie → item).

## Step G — Deploy to the app

```bash
mkdir -p ../models/tiger
cp tiger/{predictor_int8.onnx,semantic_ids.json,prefix_trie.json,item_map.json,config.json} ../models/tiger/
```

## Step H — Enable the engine

In `config.yaml`:

```yaml
recommender:
  engine_mode: fallback        # fallback | legacy_only | generative_only
  generative:
    history_threshold: 3
    top_k: 10
    max_seq_len: 10
    candidate_pool: 60
```

- `fallback` (default): generative when artifacts exist **and** history ≥
  threshold; legacy recommender otherwise or on any runtime error.
- `legacy_only`: instant kill-switch, existing behavior byte-for-byte.
- `generative_only`: generative primary; legacy still catches failures.

Verify: `POST /api/recommend` responses carry `"engine": "generative" | "legacy"`.

## Re-training cadence

- Catalogue changed (new TMDB sync/full build): rerun Steps A, C, F, G.
- New watch history only: rerun Steps D, E, F, G.
- The runtime engine loads artifacts lazily; a server restart picks up new files deterministically.

## Artifacts reference (models/tiger/)

| File | Purpose |
|---|---|
| `predictor_int8.onnx` | INT8 sequence predictor (<15 MB, <5 ms CPU) |
| `semantic_ids.json` | item_id → semantic ID tuple map |
| `prefix_trie.json` | semantic ID tuple → item IDs (no hallucinations) |
| `item_map.json` | item_id → catalog text (debugging / audits) |
| `config.json` | predictor hyper-parameters used by the runtime |
