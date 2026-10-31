# Training (runs on the Mac)

These scripts build the model files the server reads. They need `torch`, so they run on
the Mac, never on the server. You normally don't run them by hand: the **Train** button
(`ops/mac/nightly.sh`, see `ops/mac/README.md`) runs them, checks the result and sends it
to the server as a "model bundle".

Everything here is free: it uses only the Mac's own processor.

| Script | What it makes | Read by |
|---|---|---|
| `build_vibe_index.py` | `vibe/`: one vector per title from its "vibe document" (plot, tags, mood words), the 14 mood dials per title (`media_items.facets`), vibe clusters, and a small query encoder | vibe search, the questions pop-up, the taste model |
| `build_wishmatch_crowd.py` | `wishmatch/crowd.json`: for each film, the 30 films its fans also rated highly (MovieLens) | Wishmatch's "fans of X loved it" score |

## Run one by hand

```bash
# Vibe index (10-20 minutes for ~50k titles)
DATABASE_PATH=catalogue_full.db uv run --with torch --with sentence-transformers --with onnx --with onnxscript \
  python training/build_vibe_index.py --out models/vibe

# MovieLens crowd (under a second)
uv run python training/build_wishmatch_crowd.py \
  --movielens training/data/ml-latest-small --db catalogue_full.db --out models/wishmatch
```

`--skip-embed` on the vibe index recomputes the dials and clusters from the vectors it
already has (seconds), for example after new mood tags.

## Data

`training/data/ml-latest-small/` is the MovieLens "latest small" set (610 people,
100k ratings), downloaded once from https://grouplens.org/datasets/movielens/. Git ignores it.

## Removed

The TIGER engine (a small sequence model that guessed "what next" from watch history)
and its training steps were removed on 2026-10-31. Wishmatch replaced it. Its scripts
are in git history before that date.
