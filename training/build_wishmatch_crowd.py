#!/usr/bin/env python3
"""
Build the Wishmatch "crowd" data (training/build_wishmatch_crowd.py)  — run on the Mac

Wishmatch suggests films two people will both enjoy. Besides the taste vectors, it
uses a crowd signal: films that many MovieLens raters liked together. This script
turns MovieLens ratings into a short list of "people who liked X also liked Y"
neighbours per film, keyed by OUR catalogue ids.

    uv run python training/build_wishmatch_crowd.py \
        --movielens training/data/ml-latest-small --db catalogue_full.db --out models/wishmatch

Steps:
  1. MovieLens movieId -> tmdbId (links.csv) -> catalogue id (media_items, movies only).
     Ratings for films we do not have are dropped.
  2. Keep films with at least --min-ratings ratings. Subtract each user's own mean
     from their ratings, so a generous rater's 4 and a harsh rater's 4 mean what they should.
  3. Item-item cosine similarity on those centred ratings, scaled by n/(n+shrink)
     where n is how many users rated both films (few shared raters -> less trust).
     Done in row chunks so the full item x item table is never held in memory.
  4. Per film keep the top --neighbours others with similarity above zero.

Writes <out>/crowd.json. The nightly run bundles it for the Pi (ops/mac/nightly.sh).
DATABASE_PATH sets the default catalogue (else catalogue_full.db).
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

import numpy as np

CHUNK = 512  # films per block; a block is CHUNK x films floats, a few tens of MB


def _read_csv(path: str):
    with open(path, newline="", encoding="utf-8") as f:
        yield from csv.DictReader(f)


def build(movielens: str, db: str, neighbours: int, min_ratings: int, shrink: float):
    """Returns (neighbour table, ratings read, films matched)."""
    ratings_path = os.path.join(movielens, "ratings.csv")
    links_path = os.path.join(movielens, "links.csv")
    for p in (ratings_path, links_path):
        if not os.path.exists(p):
            raise SystemExit(f"build_wishmatch_crowd: missing {p}")
    if not os.path.exists(db):
        raise SystemExit(f"build_wishmatch_crowd: catalogue database not found: {db}")

    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        by_tmdb = {int(t): int(i) for i, t in conn.execute(
            "SELECT id, tmdb_id FROM media_items WHERE media_type='movie' AND tmdb_id IS NOT NULL")}
    finally:
        conn.close()

    ml_to_cat: Dict[int, int] = {}
    for row in _read_csv(links_path):
        try:
            tmdb = int(row["tmdbId"])
            movie = int(row["movieId"])
        except (ValueError, KeyError, TypeError):
            continue
        if tmdb in by_tmdb:
            ml_to_cat[movie] = by_tmdb[tmdb]

    users: List[int] = []
    films: List[int] = []
    values: List[float] = []
    read = 0
    for row in _read_csv(ratings_path):
        read += 1
        cat = ml_to_cat.get(int(row["movieId"]))
        if cat is None:
            continue
        users.append(int(row["userId"]))
        films.append(cat)
        values.append(float(row["rating"]))
    if not users:
        return {}, read, 0

    u = np.asarray(users)
    f = np.asarray(films)
    r = np.asarray(values, dtype=np.float32)
    # films with enough ratings
    cat_ids, f_idx, counts = np.unique(f, return_inverse=True, return_counts=True)
    keep = counts >= min_ratings
    row_ok = keep[f_idx]
    u, f_idx, r = u[row_ok], f_idx[row_ok], r[row_ok]
    remap = np.cumsum(keep) - 1
    f_idx = remap[f_idx]
    cat_ids = cat_ids[keep]
    n_films = len(cat_ids)
    if n_films == 0:
        return {}, read, 0
    _, u_idx = np.unique(u, return_inverse=True)
    n_users = int(u_idx.max()) + 1

    # centre by each user's mean (over the films kept)
    user_mean = np.bincount(u_idx, weights=r, minlength=n_users) / np.maximum(np.bincount(u_idx, minlength=n_users), 1)
    centred = (r - user_mean[u_idx]).astype(np.float32)

    X = np.zeros((n_users, n_films), dtype=np.float32)   # centred ratings
    B = np.zeros((n_users, n_films), dtype=np.float32)   # 1 where rated
    X[u_idx, f_idx] = centred
    B[u_idx, f_idx] = 1.0
    norms = np.sqrt((X * X).sum(axis=0))
    norms[norms == 0] = 1.0

    table: Dict[str, List[list]] = {}
    for start in range(0, n_films, CHUNK):
        stop = min(start + CHUNK, n_films)
        dots = X[:, start:stop].T @ X                     # chunk x films
        both = B[:, start:stop].T @ B                      # users who rated both
        sims = dots / (norms[start:stop, None] * norms[None, :])
        sims *= both / (both + shrink)
        for k in range(stop - start):
            sims[k, start + k] = 0.0                       # not its own neighbour
        for k in range(stop - start):
            row = sims[k]
            cand = np.flatnonzero(row > 0)
            if cand.size == 0:
                continue
            if cand.size > neighbours:
                cand = cand[np.argpartition(row[cand], -neighbours)[-neighbours:]]
            cand = cand[np.argsort(-row[cand])]
            out = [[int(cat_ids[j]), round(float(row[j]), 3)] for j in cand]
            out = [p for p in out if p[1] > 0]
            if out:
                table[str(int(cat_ids[start + k]))] = out
    return table, read, n_films


def write_json(path: str, payload: dict) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, separators=(",", ":"))
    os.replace(tmp, path)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Build Wishmatch crowd neighbours from MovieLens ratings")
    ap.add_argument("--movielens", required=True, help="folder with ratings.csv and links.csv")
    ap.add_argument("--db", default=os.environ.get("DATABASE_PATH", "catalogue_full.db"))
    ap.add_argument("--out", required=True, help="folder to write crowd.json into")
    ap.add_argument("--neighbours", type=int, default=30)
    ap.add_argument("--min-ratings", type=int, default=5)
    ap.add_argument("--shrink", type=float, default=10.0)
    a = ap.parse_args(argv)

    t0 = time.time()
    table, read, matched = build(a.movielens, a.db, a.neighbours, a.min_ratings, a.shrink)
    payload = {
        "source": "MovieLens ml-latest-small",
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "films": len(table),
        "neighbours": table,
    }
    path = os.path.join(a.out, "crowd.json")
    write_json(path, payload)
    print(f"ratings read: {read}; films matched (enough ratings): {matched}; films written: {len(table)}; "
          f"{time.time() - t0:.1f}s -> {path}")

    # sanity check by eye: neighbours of a few well-known films
    conn = sqlite3.connect(f"file:{a.db}?mode=ro", uri=True)
    try:
        titles = dict(conn.execute("SELECT id, title FROM media_items WHERE media_type='movie'"))
    finally:
        conn.close()
    shown = 0
    for name in ("Toy Story", "The Matrix", "The Godfather", "Pulp Fiction", "Titanic", "Forrest Gump"):
        fid = next((k for k in table if titles.get(int(k)) == name), None)
        if fid is None:
            continue
        near = ", ".join(titles.get(n, str(n)) for n, _ in table[fid][:5])
        print(f"  {name}: {near}")
        shown += 1
        if shown == 3:
            break
    return 0


if __name__ == "__main__":
    sys.exit(main())
