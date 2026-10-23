"""
Wishmatch crowd build (training/build_wishmatch_crowd.py) on a tiny made-up MovieLens

Users who like film A also like B and dislike C. Checks the output file, the
neighbour order, the id types, and that unknown / rarely rated films are skipped.
Also checks the Mac client bundles wishmatch/crowd.json.

    uv run python tests/test_wishmatch_crowd.py
"""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "training"))

from database import DatabaseManager  # noqa: E402
import build_wishmatch_crowd as crowd  # noqa: E402
from ops.mac import cc  # noqa: E402

failures = []


def check(label, condition, detail=""):
    if condition:
        print(f"  ✅ {label}")
    else:
        print(f"  ❌ {label} — got: {detail}")
        failures.append(label)


def main():
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "cat.db")
    dm = DatabaseManager(db)
    # tmdb ids 101..104 are in the catalogue; 105 (MovieLens only) is not
    dm.upsert_catalogue_items([
        {"media_type": "movie", "tmdb_id": t, "title": n, "overview": "x", "genres": "Drama",
         "rating": 7.0, "vote_count": 100}
        for t, n in ((101, "A"), (102, "B"), (103, "C"), (104, "Rare"))
    ])
    import sqlite3
    ids = {t: i for i, t in sqlite3.connect(db).execute("SELECT id, tmdb_id FROM media_items")}

    ml = os.path.join(tmp, "ml")
    os.makedirs(ml)
    with open(os.path.join(ml, "links.csv"), "w") as f:
        f.write("movieId,imdbId,tmdbId\n1,1,101\n2,2,102\n3,3,103\n4,4,104\n5,5,105\n")
    rows = ["userId,movieId,rating,timestamp"]
    for u in range(1, 13):          # 12 users like A and B, dislike C, and like/dislike a filler pattern
        likes = u % 2 == 0
        rows += [f"{u},1,5.0,0", f"{u},2,5.0,0", f"{u},3,1.0,0", f"{u},5,{4.0 if likes else 3.0},0"]
        if u == 1:
            rows.append(f"{u},4,4.0,0")   # film 4 has a single rating
    with open(os.path.join(ml, "ratings.csv"), "w") as f:
        f.write("\n".join(rows) + "\n")

    out = os.path.join(tmp, "out")
    rc = crowd.main(["--movielens", ml, "--db", db, "--out", out, "--min-ratings", "5"])
    path = os.path.join(out, "crowd.json")
    check("exit code 0", rc == 0, rc)
    check("crowd.json exists", os.path.exists(path))
    data = json.load(open(path))
    nb = data["neighbours"]
    a, b, c = str(ids[101]), str(ids[102]), str(ids[103])
    check("header fields", data["source"] == "MovieLens ml-latest-small" and data["films"] == len(nb) and "built_at" in data)
    check("B is A's top neighbour", nb.get(a) and nb[a][0][0] == ids[102], nb.get(a))
    check("C is not a positive neighbour of A", all(n != ids[103] for n, _ in nb.get(a, [])), nb.get(a))
    check("keys are catalogue ids as strings", all(isinstance(k, str) and k.isdigit() for k in nb))
    check("neighbour ids are ints, sims are floats", all(isinstance(n, int) and isinstance(s, float)
                                                         for v in nb.values() for n, s in v))
    check("film missing from the catalogue is skipped", all(k in {str(i) for i in ids.values()} for k in nb)
          and all(n in ids.values() for v in nb.values() for n, _ in v))
    check("film under --min-ratings is skipped", str(ids[104]) not in nb
          and all(n != ids[104] for v in nb.values() for n, _ in v))
    check("A is not its own neighbour", all(n != ids[101] for n, _ in nb[a]))
    check("no temp file left behind", not os.path.exists(path + ".tmp"))

    missing = os.path.join(tmp, "nope")
    try:
        crowd.main(["--movielens", missing, "--db", db, "--out", out])
        check("missing csv exits non-zero", False)
    except SystemExit as e:
        check("missing csv exits non-zero", e.code not in (0, None), e.code)

    print("bundle")
    import tarfile
    bundle = os.path.join(tmp, "b.tar.gz")
    manifest = cc.build_bundle(bundle, metrics={}, tiger=None, vibe=None, db_before=None, db_after=None,
                               gate="passed", source_db=None, wishmatch=out)
    check("bundle lists wishmatch/crowd.json", list(manifest["files"]) == ["wishmatch/crowd.json"], manifest["files"])
    with tarfile.open(bundle) as t:
        check("tarball holds it", "wishmatch/crowd.json" in t.getnames(), t.getnames())

    if failures:
        print(f"\n{len(failures)} failed")
        sys.exit(1)
    print("\nall passed")


main()
