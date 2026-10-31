"""
Round trip of the Mac client (ops/mac/cc.py) against a real local server

Starts the app on a free port with a throwaway database, then: export, change the copy
the way a catalogue script would, diff, push, and build and push a model bundle.

    uv run python tests/test_mac_client.py
"""

import json
import os
import shutil
import socket
import sqlite3
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

failures = []


def check(label, condition, detail=""):
    if condition:
        print(f"  ✅ {label}")
    else:
        print(f"  ❌ {label} — got: {detail}")
        failures.append(label)


with tempfile.TemporaryDirectory() as tmp:
    db_file = os.path.join(tmp, "state.db")
    models = os.path.join(tmp, "models")
    cfg_file = os.path.join(tmp, "config.yaml")
    with open(cfg_file, "w", encoding="utf-8") as f:
        json.dump({
            "media_folders": [os.path.join(tmp, "Movies"), os.path.join(tmp, "Shows")],
            "jellyfin_library": {"movie_dir": os.path.join(tmp, "Movies"), "tv_dir": os.path.join(tmp, "Shows")},
            "torrent": {"download_dir": os.path.join(tmp, "downloads")},
        }, f)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    env_file = os.path.join(tmp, "env")
    with open(env_file, "w", encoding="utf-8") as f:
        f.write(f"# comment\nCC_SERVER=http://127.0.0.1:{port}\nADMIN_TOKEN=tok\n")
    os.environ.update({"CONFIG_PATH": cfg_file, "DATABASE_PATH": db_file, "MODELS_DIR": os.path.join(models, "current"),
                       "ADMIN_TOKEN": "tok", "CC_ENV_FILE": env_file})
    import server
    from web import state  # noqa: E402
    import uvicorn
    from ops.mac import cc
    from services import model_bundle

    state.vibe_index.reload = lambda: True
    state.vibe_index.is_ready = lambda: True
    model_bundle.smoke_test = lambda folder, db: None

    conn = sqlite3.connect(db_file)
    conn.execute("INSERT INTO media_items (media_type, tmdb_id, title, rating) VALUES ('movie', 1, 'Old Film', 7.0)")
    conn.execute("INSERT INTO watch_history (media_item_id, user_rating) VALUES (1, 8.0)")
    conn.commit()
    conn.close()

    srv = uvicorn.Server(uvicorn.Config(server.app, host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=srv.run, daemon=True).start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.1)

    print("export")
    copy = os.path.join(tmp, "copy.db")
    check("max id reported", cc.export_db(copy) == 1)
    before = os.path.join(tmp, "before.db")
    shutil.copy(copy, before)

    print("catalogue script on the copy, diff, push")
    c = sqlite3.connect(copy)
    c.execute("INSERT INTO media_items (media_type, tmdb_id, title, rating, vote_count) VALUES ('movie', 50, 'Found Online', 7.5, 900)")
    c.execute("UPDATE media_items SET rating = 7.4 WHERE tmdb_id = 1")
    c.execute("UPDATE watch_history SET user_rating = 1.0")   # user data on the copy must never travel
    c.commit()
    c.close()
    patch = os.path.join(tmp, "p.jsonl")
    check("two changes found", cc.diff_catalogue(before, copy, patch) == 2)
    with open(patch, encoding="utf-8") as f:
        check("only allow-listed columns travel", all("user_rating" not in line for line in f))
    check("patch applied", cc.push_patch(patch) == {"inserted": 1, "updated": 1, "unchanged": 0, "skipped": 0, "replaced_rows": 0})
    s = sqlite3.connect(db_file)
    check("new row has a server-assigned id", s.execute("SELECT id FROM media_items WHERE tmdb_id=50").fetchone() == (2,))
    check("update landed", s.execute("SELECT rating FROM media_items WHERE tmdb_id=1").fetchone() == (7.4,))
    check("server watch history untouched", s.execute("SELECT user_rating FROM watch_history").fetchone() == (8.0,))
    s.close()
    check("pushing it again changes nothing", cc.push_patch(patch)["inserted"] == 0)

    print("model bundle")
    vibe = os.path.join(tmp, "vibe")
    os.makedirs(vibe)
    with open(os.path.join(vibe, "item_ids.json"), "w") as f:
        json.dump([1, 2], f)
    with open(os.path.join(vibe, "junk.txt"), "w") as f:
        f.write("not shipped")
    metrics = os.path.join(tmp, "m.json")
    with open(metrics, "w") as f:
        json.dump({"hit_rate": 0.3}, f)
    after = os.path.join(tmp, "after.db")
    shutil.copy(copy, after)
    a = sqlite3.connect(after)
    a.execute("UPDATE media_items SET facets = '{}' WHERE tmdb_id = 1")
    a.commit()
    a.close()
    out = os.path.join(tmp, "b.tar.gz")
    manifest = cc.build_bundle(out, metrics={"hit_rate": 0.3}, vibe=vibe, db_before=copy, db_after=after,
                               gate="passed", source_db=copy)
    check("manifest lists the model files and the patch, not the junk",
          sorted(manifest["files"]) == ["db_patch.jsonl", "vibe/item_ids.json"], manifest["files"])
    check("bundle installs on the server", cc.push_bundle(out)["installed"] == manifest["bundle_id"])
    st = cc.model_status()
    check("server reports the model and its metrics", st["current"] == manifest["bundle_id"]
          and st["manifest"]["metrics"] == {"hit_rate": 0.3}, st)
    check("bundle db patch applied", sqlite3.connect(db_file).execute(
        "SELECT facets FROM media_items WHERE tmdb_id=1").fetchone() == ("{}",))

    print("errors")
    try:
        cc._request("GET", "/nope")
        check("an HTTP error becomes a clear message", False)
    except cc.CCError as e:
        check("an HTTP error becomes a clear message", "404" in str(e), str(e))
    srv.should_exit = True

if failures:
    print(f"\n{len(failures)} failed")
    sys.exit(1)
print("\nall Mac client tests passed")
