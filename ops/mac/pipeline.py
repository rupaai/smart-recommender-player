#!/usr/bin/env python3
"""Helpers for ops/mac/nightly.sh that need the project's Python environment.

    pipeline.py setting <dotted.key> [default]     read a value from config.yaml (e.g. nightly.min_rating)
    pipeline.py evaluate --db X.db --tiger DIR --taste-json F --out metrics.json
                                                   held-out numbers for the freshly trained models
    pipeline.py gate --new metrics.json --status status.json [--max-drop-pct 1.0]
                                                   exit 0 = publish, 3 = hold back (reason printed)

The metrics are a sanity gate against a model that got worse (or broke), not a proper
test set: the TIGER hit rate is measured on the same liked-film histories it trained on.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

# Higher is better for every metric the gate compares.
GATED = ("tiger_hit_rate", "taste_spearman", "taste_auc")


def setting(key: str, default: Optional[str]) -> str:
    import yaml

    with open(os.path.join(ROOT, "config.yaml"), "r", encoding="utf-8") as f:
        node: Any = yaml.safe_load(f) or {}
    for part in key.split("."):
        node = node.get(part) if isinstance(node, dict) else None
    if node is None:
        if default is None:
            raise SystemExit(f"config.yaml has no setting {key}")
        return default
    return str(node)


def tiger_hit_rate(db_path: str, tiger_dir: str, top_k: int = 40, min_context: int = 3) -> Dict[str, float]:
    """Share of liked films the model puts among its candidates, given the liked films before them."""
    from database import DatabaseManager
    from services.generative_retriever import GenerativeRetriever, trie_lookup

    retriever = GenerativeRetriever(DatabaseManager(db_path), {})
    retriever.artifact_dir = tiger_dir
    retriever.top_k = top_k
    if not retriever.is_ready():
        raise SystemExit(f"TIGER model in {tiger_dir} did not load")
    profiles = ["me", "partner", "together"]
    hits = total = 0
    for profile in profiles:
        history = retriever.get_history_item_ids(limit=300, profile=profile)
        for i in range(min_context, len(history)):
            seq = retriever.history_to_semantic_sequence(history[:i])
            target = history[i]
            if len(seq) < min_context or str(target) not in retriever._semantic_ids:
                continue
            found = set()
            for codes, _ in retriever.predict_top_tuples(seq):
                found.update(trie_lookup(retriever._trie, codes))
            hits += target in found
            total += 1
    return {"tiger_hit_rate": round(hits / total, 4), "tiger_cases": float(total)} if total else {}


def evaluate(db: str, tiger: str, taste_json: Optional[str], out: str) -> Dict[str, Any]:
    metrics: Dict[str, Any] = dict(tiger_hit_rate(db, tiger))
    info = {"tiger_cases": metrics.pop("tiger_cases", 0)}
    if taste_json and os.path.exists(taste_json):
        with open(taste_json, "r", encoding="utf-8") as f:
            metrics.update(json.load(f))
    metrics["info"] = info
    with open(out, "w", encoding="utf-8") as f:
        json.dump(metrics, f, indent=2)
    print(json.dumps(metrics))
    return metrics


def gate(new: Dict[str, Any], current: Dict[str, Any], max_drop_pct: float) -> List[str]:
    """Reasons to hold the new model back (empty = publish)."""
    reasons = []
    if not any(k in new for k in GATED):
        return ["no evaluation numbers were produced, so the model cannot be compared"]
    for key in GATED:
        if key in new and key in current and isinstance(current[key], (int, float)) and current[key] > 0:
            drop = (current[key] - new[key]) / current[key] * 100
            if drop > max_drop_pct:
                reasons.append(f"{key} fell {drop:.1f}% ({current[key]} -> {new[key]}), more than the {max_drop_pct}% allowed")
    return reasons


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("setting")
    s.add_argument("key"); s.add_argument("default", nargs="?")  # noqa: E702
    e = sub.add_parser("evaluate")
    e.add_argument("--db", required=True); e.add_argument("--tiger", required=True)  # noqa: E702
    e.add_argument("--taste-json"); e.add_argument("--out", required=True)  # noqa: E702
    g = sub.add_parser("gate")
    g.add_argument("--new", required=True); g.add_argument("--status", required=True)  # noqa: E702
    g.add_argument("--max-drop-pct", type=float, default=1.0)
    a = ap.parse_args()
    if a.cmd == "setting":
        print(setting(a.key, a.default))
    elif a.cmd == "evaluate":
        evaluate(a.db, a.tiger, a.taste_json, a.out)
    else:
        with open(a.new, "r", encoding="utf-8") as f:
            new = json.load(f)
        with open(a.status, "r", encoding="utf-8") as f:
            current = (json.load(f).get("manifest") or {}).get("metrics") or {}
        reasons = gate(new, current, a.max_drop_pct)
        if reasons:
            print("; ".join(reasons))
            return 3
        print("no metric got worse" if current else "first model with numbers on the server")
    return 0


if __name__ == "__main__":
    sys.exit(main())
