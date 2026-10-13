"""
Offline tests for the Northern Lights M2 player additions (tests/test_nl_next.py):
the next-episode lookup behind the player's card (P11) and the per-show autoplay setting (P12).

    uv run python tests/test_nl_next.py
"""

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import nl_fake  # noqa: E402
from nl_fake import EPS, SECRET  # noqa: E402

failures = []


def check(label, condition, detail=""):
    if condition:
        print(f"  ✅ {label}")
    else:
        print(f"  ❌ {label} — got: {detail}")
        failures.append(label)


def main():
    tmp = tempfile.mkdtemp(prefix="nl_next_test_")
    try:
        run(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if failures:
        print(f"\n{len(failures)} FAILED: {failures}")
        sys.exit(1)
    print("\nAll Northern Lights next-episode tests passed")


def run(tmp):
    ctx = nl_fake.boot(tmp)
    client, fake, ids, db = ctx["client"], ctx["fake"], ctx["ids"], ctx["db"]
    src = ids["sources"]
    show = ids["Sigma"]

    print("GET /api/nl/next/{id}")
    r = client.get(f"/api/nl/next/{show}", params={"source": src[EPS[(1, 1)]]})
    n = r.json()
    check("after S1E1 comes S1E2", r.status_code == 200 and n["has_next"] and n["source_id"] == src[EPS[(1, 2)]]
          and n["season"] == 1 and n["episode"] == 2 and n["code"] == "S1 · E2", r.text)
    check("name comes from Jellyfin", n["name"] == "Second" and n["show_title"] == "Sigma", n)
    check("autoplay is on by default", n["autoplay"] is True)
    n = client.get(f"/api/nl/next/{show}", params={"source": src[EPS[(1, 3)]]}).json()
    check("the next episode can be in the next season", n["has_next"] and n["season"] == 2 and n["episode"] == 1 and n["name"] == "Premiere", n)
    n = client.get(f"/api/nl/next/{show}", params={"source": src[EPS[(2, 1)]]}).json()
    check("the last episode has no next", n["has_next"] is False, n)
    n = client.get(f"/api/nl/next/{ids['Alpha']}", params={"source": src[nl_fake.ALPHA]}).json()
    check("a movie has no next", n["has_next"] is False, n)
    check("an unknown source has no next", client.get(f"/api/nl/next/{show}", params={"source": 99999}).json()["has_next"] is False)
    check("source is required", client.get(f"/api/nl/next/{show}").status_code == 422)
    fake.down = True
    ctx["server"].nl_browse._series.clear()
    n = client.get(f"/api/nl/next/{show}", params={"source": src[EPS[(1, 1)]]}).json()
    check("Jellyfin down: the card still works, just without the name", n["has_next"] and n["name"] == "" and n["code"] == "S1 · E2", n)
    fake.down = False
    check("no key in the reply", SECRET not in client.get(f"/api/nl/next/{show}", params={"source": src[EPS[(1, 1)]]}).text)

    print("autoplay per show (nl_show_prefs)")
    r = client.get(f"/api/nl/show/{show}/prefs")
    check("default is on", r.status_code == 200 and r.json() == {"autoplay": True}, r.text)
    r = client.put(f"/api/nl/show/{show}/prefs", json={"autoplay": False})
    check("turn it off", r.status_code == 200 and r.json() == {"autoplay": False}, r.text)
    check("it is remembered", client.get(f"/api/nl/show/{show}/prefs").json() == {"autoplay": False})
    check("the show page data carries it", client.get(f"/api/nl/show/{show}").json()["autoplay"] is False)
    check("the next-episode lookup carries it", client.get(f"/api/nl/next/{show}", params={"source": src[EPS[(1, 1)]]}).json()["autoplay"] is False)
    with db.get_connection() as conn:
        row = conn.execute("SELECT autoplay FROM nl_show_prefs WHERE media_item_id = ?", (show,)).fetchone()
    check("stored in the database", row is not None and row["autoplay"] == 0)
    r = client.put(f"/api/nl/show/{show}/prefs", json={"autoplay": True})
    check("turn it back on", r.json() == {"autoplay": True} and client.get(f"/api/nl/show/{show}/prefs").json()["autoplay"] is True)
    with db.get_connection() as conn:
        count = conn.execute("SELECT COUNT(*) FROM nl_show_prefs").fetchone()[0]
    check("one row per show (upsert)", count == 1, count)
    check("movies have no autoplay setting", client.put(f"/api/nl/show/{ids['Alpha']}/prefs", json={"autoplay": False}).status_code == 404)
    check("unknown show 404", client.put("/api/nl/show/9999/prefs", json={"autoplay": False}).status_code == 404)
    check("body must be a boolean", client.put(f"/api/nl/show/{show}/prefs", json={"autoplay": "maybe"}).status_code == 422
          and client.put(f"/api/nl/show/{show}/prefs", json={}).status_code == 422)
    check("another show is not affected", client.get(f"/api/nl/show/{ids['Beta']}/prefs").json() == {"autoplay": True})

    print("player page and files")
    r = client.get(f"/nl/play/{show}?source={src[EPS[(1, 2)]]}")
    check("player page has the next-episode card with Play now and Cancel",
          r.status_code == 200 and 'id="nl-next"' in r.text and 'id="nl-next-play"' in r.text and 'id="nl-next-cancel"' in r.text
          and "Next episode" in r.text and SECRET not in r.text, r.status_code)
    js = client.get("/static/nl/player.js").text
    check("player.js knows the 30 s card and 10 s countdown", "NEXT_CARD_SECONDS = 30" in js and "COUNTDOWN_SECONDS = 10" in js)
    check("player.js reads the episode from the address", 'query.get("source")' in js and "/api/nl/next/" in js)
    check("show page script has the autoplay switch", 'role", "switch"' in client.get("/static/nl/show.js").text
          and "/prefs" in client.get("/static/nl/show.js").text)


if __name__ == "__main__":
    main()
