#!/usr/bin/env bash
# Retrain the TIGER "what to watch next" model on YOUR history + MovieLens.
# Run from your own terminal (needs internet once to fetch torch):
#
#     bash training/retrain_tiger.sh
#
# Takes ~5–15 min on an Apple Silicon Mac. Restart the server afterwards.
# Re-run whenever you've rated ~20 more films — that is what "learning over
# time" means for this model.
#
# The nightly Mac job (ops/mac/nightly.sh) sets SKIP_DEPLOY=1: it only wants the new files in
# training/tiger/ and publishes them to the server itself, so Step G is skipped.
set -euo pipefail
cd "$(dirname "$0")"
export DATABASE_PATH="${DATABASE_PATH:-catalogue_full.db}"
RUN="uv run --with torch --with onnx --with onnxscript python"
ML="data/ml-latest-small/ratings.csv"

echo "== Step D: sequences (your liked films + MovieLens)"
$RUN bootstrap_sequences.py --movielens "$ML" --no-synthetic

echo "== Step E: train the predictor"
$RUN train_tiger_predictor.py --epochs 12

echo "== Step F: export ONNX + INT8 + trie (with deployment gates)"
$RUN export_tiger_onnx.py

if [ -n "${SKIP_DEPLOY:-}" ]; then
  echo "✅ Done (SKIP_DEPLOY set: models/ untouched; new files are in training/tiger/)."
  exit 0
fi

echo "== Step G: deploy (old model kept in models/tiger.bak)"
rm -rf ../models/tiger.bak && cp -R ../models/tiger ../models/tiger.bak
cp tiger/{predictor_int8.onnx,semantic_ids.json,prefix_trie.json,item_map.json,config.json} ../models/tiger/
echo "✅ Done. Restart the server: uv run python server.py"
