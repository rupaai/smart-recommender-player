"""
Offline tests for "Delete from server" (services/nl_delete.py and /api/nl/delete/{id})

    uv run python tests/test_nl_delete.py

Real files in a throw-away folder; Jellyfin is faked (tests/nl_fake.py).
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import nl_fake  # noqa: E402

failures = []


def check(label, condition, detail=""):
    if condition:
        print(f"  ✅ {label}")
    else:
        print(f"  ❌ {label} {detail}")
        failures.append(label)


def write(path, size):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"x" * size)


tmp = tempfile.mkdtemp()
ctx = nl_fake.boot(tmp)
client, state, db = ctx["client"], ctx["state"], ctx["db"]
# The folders the server really uses (make test lays its own settings over the test config).
movies, shows = state.jellyfin_service.movie_dir, state.jellyfin_service.tv_dir
rescans = []
jellyfin_away = []


def fake_rescan():
    if jellyfin_away:
        raise ConnectionError("Jellyfin is down")
    rescans.append(1)


state.library_delete.refresh = fake_rescan

film = os.path.join(movies, "Delta (2022)", "Delta (2022).mkv")
write(film, 3000)
write(os.path.join(movies, "Delta (2022)", "Delta (2022).en.srt"), 100)
db.register_source_file(film, "Delta", 2022, "movie")
eps = {}
for s, e in ((1, 1), (1, 2), (2, 1)):
    p = os.path.join(shows, "Omega", f"Season {s}", f"Omega S0{s}E0{e}.mkv")
    write(p, 1000)
    db.register_source_file(p, "Omega", 2020, "tv", season_number=s, episode_number=e)
    eps[(s, e)] = p
outside = os.path.join(tmp, "elsewhere", "Kappa.mkv")
write(outside, 500)
db.register_source_file(outside, "Kappa", 2010, "movie")
with db.get_connection() as conn:
    ids = {r["title"]: r["id"] for r in conn.execute("SELECT id, title FROM media_items")}

print("preview (deletes nothing)")
r = client.get(f"/api/nl/delete/{ids['Delta']}")
p = r.json()
check("a movie: name with year, video and subtitle counted in the size",
      r.status_code == 200 and p["what"] == "Delta (2022)" and p["bytes"] == 3100 and p["videos"] == 1 and p["episodes"] == 0, p)
check("nothing was deleted by the preview", os.path.exists(film))
p = client.get(f"/api/nl/delete/{ids['Omega']}").json()
check("a whole show: every season", p["what"] == "Omega, all 2 seasons" and p["episodes"] == 3 and p["bytes"] == 3000, p)
p = client.get(f"/api/nl/delete/{ids['Omega']}", params={"season": 1}).json()
check("one season", p["what"] == "Omega, season 1" and p["episodes"] == 2 and p["bytes"] == 2000, p)
check("a season the show doesn't have: 404", client.get(f"/api/nl/delete/{ids['Omega']}", params={"season": 9}).status_code == 404)
check("an unknown title: 404", client.get("/api/nl/delete/999999").status_code == 404)

print("the confirm step")
r = client.post(f"/api/nl/delete/{ids['Delta']}", json={})
check("without confirm: refused, file kept", r.status_code == 400 and os.path.exists(film), r.text)
r = client.post(f"/api/nl/delete/{ids['Delta']}", json={"confirm": False})
check("confirm false: refused", r.status_code == 400 and os.path.exists(film))

print("deleting a movie")
r = client.post(f"/api/nl/delete/{ids['Delta']}", json={"confirm": True})
d = r.json()
check("deleted, space freed reported", r.status_code == 200 and d["freed_bytes"] == 3100 and d["title_left"] is False, d)
check("video, subtitle and its folder are gone; Movies stays",
      not os.path.exists(os.path.dirname(film)) and os.path.isdir(movies))
check("the catalogue forgot it (gone from Movies)",
      "Delta" not in [m["title"] for m in client.get("/api/nl/library", params={"type": "movie"}).json()["items"]])
check("the title row stays, so ratings and history are kept", db.get_item_by_id(ids["Delta"]) is not None)
check("Jellyfin was asked to rescan", len(rescans) == 1, rescans)
check("asking again: 404", client.post(f"/api/nl/delete/{ids['Delta']}", json={"confirm": True}).status_code == 404)

print("deleting one season, then the rest of the show")
d = client.post(f"/api/nl/delete/{ids['Omega']}", json={"confirm": True, "season": 1}).json()
check("season 1 gone, season 2 kept", not os.path.exists(eps[(1, 1)]) and not os.path.exists(eps[(1, 2)])
      and os.path.exists(eps[(2, 1)]) and d["title_left"] is True, d)
check("the empty season folder went, the show folder stayed",
      not os.path.exists(os.path.dirname(eps[(1, 1)])) and os.path.isdir(os.path.dirname(os.path.dirname(eps[(2, 1)]))))
os.remove(eps[(2, 1)])     # removed by hand already: the catalogue row is still forgotten
d = client.post(f"/api/nl/delete/{ids['Omega']}", json={"confirm": True}).json()
check("a file already gone is forgotten anyway", d["title_left"] is False and d["kept"] == 0, d)

print("never outside the Movies and Shows folders")
d = client.post(f"/api/nl/delete/{ids['Kappa']}", json={"confirm": True}).json()
check("a file elsewhere is kept and counted", os.path.exists(outside) and d["kept"] == 1 and d["freed_bytes"] == 0, d)
check("and it stays in the catalogue", db.get_nl_title(ids["Kappa"]) is not None)

print("Jellyfin away")
film2 = os.path.join(movies, "Eta (2023)", "Eta (2023).mkv")
write(film2, 10)
db.register_source_file(film2, "Eta", 2023, "movie")
with db.get_connection() as conn:
    eta = conn.execute("SELECT id FROM media_items WHERE title = 'Eta'").fetchone()["id"]
jellyfin_away.append(1)
r = client.post(f"/api/nl/delete/{eta}", json={"confirm": True})
check("the delete still works when the rescan fails", r.status_code == 200 and not os.path.exists(film2), r.text)

print("pages carry the button")
check("web pages load the delete script", "/static/nl/delete.js" in client.get(f"/nl/title/{ids['Kappa']}").text)
check("TV pages load the delete script", "/static/nl/delete.js" in client.get(f"/nl/tv/title/{ids['Kappa']}").text)

if failures:
    print(f"\n{len(failures)} failed")
    sys.exit(1)
print("\nall passed")
