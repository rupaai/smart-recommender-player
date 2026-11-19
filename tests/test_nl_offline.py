"""
Offline tests for "Save for offline" (services/nl_offline.py and /api/nl/offline/...)

    uv run python tests/test_nl_offline.py

Real files in a throw-away folder; Jellyfin is faked (tests/nl_fake.py).
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import nl_fake  # noqa: E402
from services.nl_offline import safe_name  # noqa: E402
from urllib.parse import unquote  # noqa: E402

failures = []


def check(label, condition, detail=""):
    if condition:
        print(f"  ✅ {label}")
    else:
        print(f"  ❌ {label} {detail}")
        failures.append(label)


def write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


tmp = tempfile.mkdtemp()
ctx = nl_fake.boot(tmp)
client, state, db = ctx["client"], ctx["state"], ctx["db"]
movies, shows = state.jellyfin_service.movie_dir, state.jellyfin_service.tv_dir

film_bytes = bytes(range(256)) * 20
film = os.path.join(movies, "Delta.2022.1080p.REMUX", "Delta.2022.1080p.REMUX.mkv")
write(film, film_bytes)
write(os.path.join(movies, "Delta.2022.1080p.REMUX", "Delta.2022.1080p.REMUX.en.srt"), b"1\n00:00:01,000 --> 00:00:02,000\nHi\n")
write(os.path.join(movies, "Delta.2022.1080p.REMUX", "Delta.2022.1080p.REMUX.nfo"), b"<movie/>")
db.register_source_file(film, "Delta: Part 1", 2022, "movie")
for s, e in ((1, 1), (1, 2), (2, 1)):
    p = os.path.join(shows, "Omega", f"Season {s}", f"Omega S0{s}E0{e}.mp4")
    write(p, b"e" * 1000)
    db.register_source_file(p, "Omega", 2020, "tv", season_number=s, episode_number=e)
outside = os.path.join(tmp, "elsewhere", "Kappa.mkv")
write(outside, b"k" * 500)
db.register_source_file(outside, "Kappa", 2010, "movie")
with db.get_connection() as conn:
    ids = {r["title"]: r["id"] for r in conn.execute("SELECT id, title FROM media_items")}
    sources = {r["file_path"]: r["id"] for r in conn.execute("SELECT id, file_path FROM media_sources")}

print("what can be saved")
r = client.get(f"/api/nl/offline/{ids['Delta: Part 1']}")
p = r.json()
f = p["files"][0] if r.status_code == 200 and p.get("files") else {}
check("a movie: one file, plain name with the year, reserved signs taken out",
      r.status_code == 200 and len(p["files"]) == 1 and f["name"] == "Delta Part 1 (2022).mkv", p)
check("its size counts the subtitle, not the .nfo", f.get("bytes") == len(film_bytes) and p["bytes"] == len(film_bytes) + 35, p)
check("the subtitle is named like the saved video, so players load it",
      [s["name"] for s in f.get("subtitles", [])] == ["Delta Part 1 (2022).en.srt"], f)
p = client.get(f"/api/nl/offline/{ids['Omega']}", params={"season": 1}).json()
check("one season: its episodes in order, named Show - SxxEyy",
      [x["name"] for x in p["files"]] == ["Omega - S01E01.mp4", "Omega - S01E02.mp4"] and p["what"] == "Omega, season 1", p)
p = client.get(f"/api/nl/offline/{ids['Omega']}").json()
check("a whole show: every episode", len(p["files"]) == 3 and p["bytes"] == 3000, p)
check("a season the show doesn't have: 404", client.get(f"/api/nl/offline/{ids['Omega']}", params={"season": 9}).status_code == 404)
check("an unknown title: 404", client.get("/api/nl/offline/999999").status_code == 404)
p = client.get(f"/api/nl/offline/{ids['Kappa']}").json()
check("a file outside the Movies and Shows folders is not offered", p["files"] == [], p)

print("saving a file")
r = client.get(f"/api/nl/offline/file/{sources[film]}")
check("the original bytes, untouched", r.status_code == 200 and r.content == film_bytes, r.status_code)
cd = unquote(r.headers.get("content-disposition", ""))
check("sent as a download with the plain name", cd.startswith("attachment") and "Delta Part 1 (2022).mkv" in cd, cd)
r = client.get(f"/api/nl/offline/file/{sources[film]}", headers={"Range": "bytes=100-199"})
check("a broken download can go on (Range answered)", r.status_code == 206 and r.content == film_bytes[100:200], r.status_code)
r = client.get(f"/api/nl/offline/subtitle/{sources[film]}/0")
check("the subtitle file, under the matching name",
      r.status_code == 200 and b"Hi" in r.content and "Delta Part 1 (2022).en.srt" in unquote(r.headers.get("content-disposition", "")))
check("a subtitle number that isn't there: 404", client.get(f"/api/nl/offline/subtitle/{sources[film]}/5").status_code == 404)
check("a file outside the folders is never sent", client.get(f"/api/nl/offline/file/{sources[outside]}").status_code == 404)
check("an unknown file: 404", client.get("/api/nl/offline/file/999999").status_code == 404)
os.remove(os.path.join(shows, "Omega", "Season 2", "Omega S02E01.mp4"))
check("a file gone from the disk: 404", client.get(f"/api/nl/offline/file/{sources[os.path.join(shows, 'Omega', 'Season 2', 'Omega S02E01.mp4')]}").status_code == 404)
check("nothing was changed on the server", os.path.exists(film) and len(os.listdir(os.path.dirname(film))) == 3)

print("names")
check("slashes and colons never reach a file name", safe_name('A/B: "C"?') == "A B C")
check("an empty name still gives a name", safe_name("...") == "Video")

print("pages")
for path in ("/nl/", "/player/"):
    html = client.get(path).text
    check(f"{path} loads the Save for offline script", "/static/nl/offline.js" in html)
js = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static", "nl", "tv", "tv.js")).read()
check("the TV pages show the button only in the laptop player", "window.NLOffline && LAPTOP" in js)

if failures:
    print(f"\n{len(failures)} check(s) failed")
    sys.exit(1)
print("\nall checks passed")
