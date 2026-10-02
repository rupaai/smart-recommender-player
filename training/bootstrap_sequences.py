#!/usr/bin/env python3
"""
TIGER — Interaction Sequence Builder (training/bootstrap_sequences.py)

Builds chronological per-user watch sequences of semantic ID tuples for
Stage C training. Three data sources, in order of quality:

1. Real watch history from the SQLite catalogue (watch_history table).
2. Optional MovieLens ratings.csv mapped to catalog item IDs by (title, year)
   — recommended when local history is small (< a few hundred events).
3. Synthetic genre co-watch bootstrap as a zero-download last resort, so the
   pipeline always produces trainable sequences offline.

Output (training/tiger/):
    sequences.json  [[ [c1,c2,c3], [c1,c2,c3], ... ], ...]

Usage:
    python bootstrap_sequences.py [--movielens ratings.csv] [--min-seq 3]
"""

import argparse
import csv
import json
import os
import re
import sys
from collections import defaultdict

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import DatabaseManager  # noqa: E402


def load_semantic_ids(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)["items"]


def normalize_title(t: str) -> str:
    """Lowercased alphanumeric-only title for fuzzy MovieLens matching."""
    return re.sub(r"[^a-z0-9]", "", (t or "").lower())


def load_catalog(db) -> dict:
    """(normalized_title, year) -> item_id lookup from media_items."""
    items = db.get_reference_titles()
    catalog = {}
    for it in items:
        key = (normalize_title(it["title"]), int(it.get("release_year") or 0))
        catalog[key] = int(it["id"])
    return catalog


def real_history_sequences(db, sid: dict, min_seq: int) -> list:
    """Chronological semantic ID sequences built from watch_history."""
    history = db.get_watch_history_chronological(limit=10000)
    by_user: dict = defaultdict(list)
    for row in history:
        codes = sid.get(str(int(row["media_item_id"])))
        if codes:
            by_user["local"].append([int(c) for c in codes[:3]])
    seqs = []
    for _, codes in by_user.items():
        # collapse consecutive duplicates
        collapsed = [c for i, c in enumerate(codes) if i == 0 or c != codes[i - 1]]
        if len(collapsed) >= min_seq:
            seqs.append(collapsed)
    print(f"   real watch-history sequences: {len(seqs)}")
    return seqs


def movielens_sequences(ratings_csv: str, catalog: dict, sid: dict, min_seq: int) -> list:
    """MovieLens ratings.csv -> per-user chronological semantic ID sequences.

    Movies are matched to the catalog by (normalized title, year). Rows are
    sorted by timestamp; ratings >= 4.0 count as a positive watch event.
    """
    by_user: dict = defaultdict(list)
    matched = unmatched = 0
    with open(ratings_csv, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ml_title = row["title"]
            m = re.match(r"^(?P<t>.*)\s\((?P<y>\d{4})\)\s*$", ml_title)
            if not m:
                unmatched += 1
                continue
            year = int(m.group("y"))
            item_id = catalog.get((normalize_title(m.group("t")), year))
            if item_id is None:
                unmatched += 1
                continue
            matched += 1
            try:
                rating = float(row["rating"])
            except (KeyError, ValueError):
                rating = 4.0
            if rating >= 4.0:
                by_user[int(row["userId"])].append((int(row.get("timestamp") or 0), item_id))
    seqs = []
    for _, events in by_user.items():
        events.sort()
        codes = [sid[str(item_id)] for _, item_id in events if str(item_id) in sid]
        codes = [[int(c) for c in t[:3]] for t in codes]
        if len(codes) >= min_seq:
            seqs.append(codes)
    print(f"   MovieLens: {matched} matched / {unmatched} unmatched rows -> {len(seqs)} sequences")
    return seqs


def synthetic_sequences(db, sid: dict, min_seq: int, n_users: int = 500, seq_len: int = 12) -> list:
    """Zero-download fallback: simulated co-watch sequences.

    Each synthetic 'user' samples a favourite genre, then watches items of
    that genre (80% chance) or a random item — mimicking genre-consistent
    co-watch behaviour so the predictor learns genre-level transitions.
    """
    items = db.get_reference_titles()
    by_genre: dict = defaultdict(list)
    for it in items:
        codes = sid.get(str(int(it["id"])))
        if not codes:
            continue
        for g in (it.get("genres") or "").split(","):
            g = g.strip().lower()
            if g:
                by_genre[g].append([int(c) for c in codes[:3]])
    if not by_genre:
        return []
    rng = np.random.default_rng(42)
    genres = sorted(by_genre)
    seqs = []
    for _ in range(n_users):
        fav = genres[int(rng.integers(len(genres)))]
        other = [g for g in genres if g != fav]
        length = int(rng.integers(min_seq, seq_len + 1))
        codes = []
        for _ in range(length):
            pool = by_genre[fav] if rng.random() < 0.8 else by_genre[other[int(rng.integers(len(other)))]]
            codes.append(pool[int(rng.integers(len(pool)))])
        if len(codes) >= min_seq:
            seqs.append(codes)
    print(f"   synthetic genre co-watch sequences: {len(seqs)}")
    return seqs


def main() -> None:
    ap = argparse.ArgumentParser(description="TIGER: interaction sequence builder")
    ap.add_argument("--movielens", default=None, help="Path to MovieLens ratings.csv")
    ap.add_argument("--min-seq", type=int, default=3)
    ap.add_argument("--db", default=None)
    args = ap.parse_args()

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    here = os.path.dirname(os.path.abspath(__file__))
    out_dir = os.path.join(here, "tiger")
    sid_path = os.path.join(out_dir, "semantic_ids.json")
    if not os.path.exists(sid_path):
        print("❌ semantic_ids.json missing — run train_rqvae.py first.")
        sys.exit(1)
    sid = load_semantic_ids(sid_path)
    print(f"📊 Semantic IDs: {len(sid)} items")

    db_path = args.db or os.environ.get("DATABASE_PATH") or "catalogue.db"
    if not os.path.isabs(db_path):
        db_path = os.path.join(root, db_path)
    db = DatabaseManager(db_path)

    print("\n1️⃣  Real watch history ...")
    sequences = real_history_sequences(db, sid, args.min_seq)

    if args.movielens:
        print(f"\n2️⃣  MovieLens ({args.movielens}) ...")
        catalog = load_catalog(db)
        sequences.extend(movielens_sequences(args.movielens, catalog, sid, args.min_seq))

    if len(sequences) < 50:
        print("\n3️⃣  Synthetic genre co-watch bootstrap (sparse history fallback) ...")
        sequences.extend(synthetic_sequences(db, sid, args.min_seq))

    if not sequences:
        print("❌ No sequences could be built.")
        sys.exit(1)

    with open(os.path.join(out_dir, "sequences.json"), "w", encoding="utf-8") as f:
        json.dump(sequences, f)
    print(f"\n✅ Sequences: {len(sequences)} -> {os.path.join(out_dir, 'sequences.json')}")
    print("Next: python train_tiger_predictor.py")


if __name__ == "__main__":
    main()

