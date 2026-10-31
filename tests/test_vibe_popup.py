"""
Film features and the questions-session store (services/vibe_popup.py)

    uv run python tests/test_vibe_popup.py
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import services.vibe_popup as vp  # noqa: E402

failures = []


def check(label, condition, detail=""):
    if condition:
        print(f"  ✅ {label}")
    else:
        print(f"  ❌ {label} — got: {detail}")
        failures.append(label)


print("features")
film = {"facets": '{"humour": 0.8, "bad": "x"}', "vibe_tags": '{"dials": {"humour": 0.1, "pace": 0.4}, "feels": ["cosy"],'
        ' "flags": ["gore_heavy"]}', "genres": "Comedy, Drama", "release_year": 2001, "media_type": "tv"}
fx = vp.features(film)
check("facets win over the tags' dials, which fill gaps", fx["facets"] == {"humour": 0.8, "pace": 0.4}, fx["facets"])
check("tag words and flags are read", fx["feels"] == {"cosy"} and fx["flags"] == {"gore heavy"} and fx["tagged"], fx)
check("genres, year and type", fx["genres"] == {"comedy", "drama"} and fx["year"] == 2001 and fx["tv"], fx)
check("computed once per film", vp.features(film) is fx)
check("a film with nothing still works", vp.features({})["facets"] == {} and not vp.features({})["tagged"])


class Session:
    def __init__(self, sid, updated):
        self.id, self.updated = sid, updated


print("session store")
store = vp.SessionStore()
store.put(Session(1, time.time()))
check("a session is found by its id", store.get(1).id == 1)
check("no id, no session", store.get(None) is None and store.get(2) is None)
store.put(Session(3, time.time() - vp.SESSION_TTL - 1))
store.put(Session(4, time.time()))
check("old sessions are dropped when a new one comes in", store.get(3) is None and store.get(4) is not None)

if failures:
    print(f"\n{len(failures)} failed")
    sys.exit(1)
print("\nall vibe_popup tests passed")
