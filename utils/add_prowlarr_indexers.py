"""
Adds the safe Prowlarr indexer set (idempotent utility).

Safe set = no Cloudflare bypass / no accounts required:
YTS, EZTV, ThePirateBay, NyaaSi, Knaben.
Each added indexer is live-test-searched; failures are auto-disabled.

Run:  uv run python utils/add_prowlarr_indexers.py
"""

import os
import sys

import requests
from dotenv import load_dotenv

load_dotenv()

BASE = (os.getenv("PROWLARR_URL") or "").rstrip("/")
KEY = os.getenv("PROWLARR_API_KEY")
H = {"X-Api-Key": KEY}

# (friendly name, Prowlarr definitionName, [(test query, category)])
# Note: NyaaSi is anime-only — test it with anime titles, not live-action TV.
SAFE_INDEXERS = [
    ("YTS", "yts", [("Interstellar", "2000")]),
    ("EZTV", "eztv", [("Breaking Bad", "5000")]),
    ("ThePirateBay", "thepiratebay", [("Interstellar", "2000"), ("Breaking Bad", "5000")]),
    ("NyaaSi", "nyaasi", [("One Piece", "5000"), ("Frieren", "5000")]),
    ("Knaben", "knaben", [("Interstellar", "2000"), ("Breaking Bad", "5000")]),
]


def main() -> int:
    if not BASE or not KEY:
        print("PROWLARR_URL / PROWLARR_API_KEY not set in .env — nothing to do.")
        return 1

    existing = requests.get(f"{BASE}/api/v1/indexer", headers=H, timeout=10).json()
    have = {i.get("definitionName") for i in existing}
    print("existing indexers:", [(i.get("name"), i.get("definitionName")) for i in existing])

    schema = requests.get(f"{BASE}/api/v1/indexer/schema", headers=H, timeout=20).json()

    # Indexers require a valid appProfileId (e.g. the default "Standard" profile)
    profiles = requests.get(f"{BASE}/api/v1/appProfile", headers=H, timeout=10).json()
    profile_id = profiles[0]["id"] if profiles else None
    print("app profiles:", [(p["id"], p.get("name")) for p in profiles])
    if not profile_id:
        print("WARNING: no app profiles found — cannot add indexers.")
        return 1

    added = []
    for friendly, definition, kinds in SAFE_INDEXERS:
        if definition.lower() in {h.lower() for h in have}:
            print(f"skip {definition} (already present)")
            continue
        entry = next(
            (d for d in schema if (d.get("definitionName") or "").lower() == definition.lower()),
            None,
        )
        if entry is None:
            print(f"WARNING: definition '{definition}' not in schema — skipped")
            continue
        payload = dict(entry)
        payload.pop("id", None)
        payload["name"] = friendly
        payload["enable"] = True
        payload["tags"] = []
        payload["appProfileId"] = profile_id
        resp = requests.post(f"{BASE}/api/v1/indexer", headers=H, json=payload, timeout=20)
        if resp.status_code in (200, 201):
            print(f"added {definition}: HTTP {resp.status_code}")
            added.append((friendly, definition, kinds, resp.json().get("id")))
        else:
            print(f"FAILED to add {definition}: HTTP {resp.status_code} {resp.text[:120]}")

    # Live test each newly added indexer; disable failures (never delete).
    for friendly, definition, tests_for_indexer, idx_id in added:
        total = 0
        for query, category in tests_for_indexer:
            r = requests.get(
                f"{BASE}/api/v1/search",
                headers=H,
                params={
                    "query": query,
                    "type": "search",
                    "categories": category,
                    "limit": 20,
                    "indexerIds": idx_id,
                },
                timeout=20,
            )
            results = r.json() if r.status_code == 200 else []
            total += len(results)
            print(f"test {definition} ['{query}' cat {category}]: {len(results)} results")
        if total == 0:
            print(f"WARNING: '{definition}' returned nothing — disabling (not deleting)")
            full = requests.get(f"{BASE}/api/v1/indexer/{idx_id}", headers=H, timeout=10).json()
            full["enable"] = False
            requests.put(f"{BASE}/api/v1/indexer/{idx_id}", headers=H, json=full, timeout=10)

    print("done — healthy set:", [d for _, d, k, _ in added])
    return 0


if __name__ == "__main__":
    sys.exit(main())