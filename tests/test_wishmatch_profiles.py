"""
Written taste profiles tests (tests/test_wishmatch_profiles.py)

Storage (migration v22, set/get), utils/wishmatch_profiles.py (estimate-only path makes no API
call; the --yes path with a fake client stores validated profiles and skips hand-edited ones),
the GET / PUT /api/rec/movies/profile/<id> routes, and that a declared dislike moves the order
of a person with no ratings. Never calls the Anthropic API.

    uv run python tests/test_wishmatch_profiles.py
"""

import contextlib
import io
import json
import os
import sys
import tempfile
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

from database import DatabaseManager  # noqa: E402
import test_wishmatch_movies as twm  # noqa: E402
from services.recommender_service import MOOD_MAPPINGS  # noqa: E402
from services.wishmatch_movies import WishmatchMovies  # noqa: E402
from services.wishmatch_movies.adapter import MovieAdapter  # noqa: E402
from utils import wishmatch_profiles as wp  # noqa: E402

failures = []


def check(label, condition, detail=""):
    if condition:
        print(f"  ✅ {label}")
    else:
        print(f"  ❌ {label} — got: {detail}")
        failures.append(label)


class FakeClient:
    """Shaped like anthropic.Anthropic(): client.messages.create(...) -> .content[0].text, .usage, .stop_reason."""

    def __init__(self, answer, stop_reason="end_turn"):
        self.answer, self.stop_reason, self.calls = answer, stop_reason, []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **params):
        self.calls.append(params)
        return SimpleNamespace(content=[SimpleNamespace(text=json.dumps(self.answer))], stop_reason=self.stop_reason,
                               usage=SimpleNamespace(input_tokens=1000, output_tokens=200))


def run(argv, api=None, ollama=None):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = wp.main(argv, api=api, ollama=ollama)
    return code, buf.getvalue()


class FakeOllama:
    def __init__(self, answer):
        self.answer, self.calls = answer, 0

    def generate(self, prompt, schema=None, system=""):
        self.calls += 1
        return json.dumps(self.answer)


def main():
    db = twm.catalogue()
    ids = twm.ids_by_title(db)

    print("\n[1] Storage")
    with db.get_connection() as conn:
        table = conn.execute("SELECT name FROM sqlite_master WHERE name = 'wishmatch_profiles'").fetchone()
    check("the wishmatch_profiles table exists", table is not None)
    check("schema is 22 or newer", db.get_schema_version() >= 22, db.get_schema_version())
    check("no profile yet gives None", db.get_wishmatch_profile(1) is None)
    db.set_wishmatch_profile(1, "You like cosy things.", ["cozy", "silly"], ["tense"], "claude-haiku-5-5", False)
    got = db.get_wishmatch_profile(1)
    check("round trip: summary, lists, model, edited",
          got and got["summary"] == "You like cosy things." and got["likes"] == ["cozy", "silly"]
          and got["dislikes"] == ["tense"] and got["model"] == "claude-haiku-5-5" and got["edited"] is False, got)
    db.set_wishmatch_profile(1, "Edited.", [], [], None, True)
    got = db.get_wishmatch_profile(1)
    check("setting again replaces it", got["summary"] == "Edited." and got["likes"] == [] and got["edited"] is True, got)
    DatabaseManager(db.db_path)
    check("running the migration again keeps the profile", db.get_wishmatch_profile(1)["summary"] == "Edited.")

    print("\n[2] The script: estimate only")
    pdb = twm.catalogue()
    pids = twm.ids_by_title(pdb)
    for i in range(12):
        twm.rate(pdb, 1, pids[f"Funny Film {i}"], 9)
        twm.rate(pdb, 1, pids[f"Tense Film {i}"], 3)
    twm.rate(pdb, 1, pids["Horror Film"], 1)
    api = FakeClient({"summary": "x", "likes": [], "dislikes": []})
    code, out = run(["--db", pdb.db_path, "--haiku"], api=api)
    check("exits 0 and calls nothing", code == 0 and not api.calls, (code, len(api.calls)))
    check("prints the command with --yes", "--yes" in out and "wishmatch_profiles.py" in out, out)
    cost = float(out.split("estimated cost $")[1].split(" ")[0])
    check("prints a cost under one cent", 0 < cost < 0.01, cost)
    check("stores nothing without --yes", pdb.get_wishmatch_profile(1) is None)

    print("\n[3] The script: --yes with a fake client")
    answer = {"summary": " ".join(["You enjoy light and funny films."] * 40),
              "likes": ["silly", "witty", "bogus", "silly", "tense"], "dislikes": ["tense", "dread", "nonsense"]}
    api = FakeClient(answer)
    code, out = run(["--db", pdb.db_path, "--haiku", "--yes"], api=api)
    check("one call, for the one person with ratings", code == 0 and len(api.calls) == 1, len(api.calls))
    call = api.calls[0]
    check("Haiku, thinking off, JSON schema, no batch",
          call["model"] == wp.MODEL and call["thinking"] == {"type": "disabled"}
          and call["output_config"]["format"]["type"] == "json_schema", list(call))
    prompt = call["messages"][0]["content"]
    check("the prompt lists loved and disliked titles with scores and vibe lines",
          "Funny Film 0" in prompt and "Tense Film 0" in prompt and "[+0.7]" in prompt and "[-1.0]" in prompt and "in one line" in prompt, prompt[:300])
    check("at most 40 loved and 20 disliked", prompt.count("\n- Funny Film") <= 40 and prompt.count("\n- Tense Film") <= 20,
          (prompt.count("\n- Funny Film"), prompt.count("\n- Tense Film")))
    stored = pdb.get_wishmatch_profile(1)
    check("stored with validated words: unknown and repeated dropped, a word in both stays a like",
          stored["likes"] == ["silly", "witty", "tense"] and stored["dislikes"] == ["dread"], stored)
    check("summary cut to 90 words", len(stored["summary"].split()) == 90, len(stored["summary"].split()))
    check("stored as machine-made", stored["edited"] is False and stored["model"] == wp.MODEL, stored)
    check("prints the real cost", "Spent $" in out, out)
    check("a person without ratings gets no profile", pdb.get_wishmatch_profile(2) is None)

    print("\n[4] Hand-edited profiles are kept")
    pdb.set_wishmatch_profile(1, "My own words.", ["cozy"], [], None, True)
    api = FakeClient(answer)
    code, out = run(["--db", pdb.db_path, "--haiku", "--yes"], api=api)
    check("no call and the edit is kept", not api.calls and pdb.get_wishmatch_profile(1)["summary"] == "My own words.", out)
    code, out = run(["--db", pdb.db_path, "--haiku", "--yes", "--overwrite-edited"], api=FakeClient(answer))
    check("--overwrite-edited replaces it", pdb.get_wishmatch_profile(1)["edited"] is False, out)

    print("\n[5] A declared dislike moves a person with no ratings")
    wm = WishmatchMovies(db, MOOD_MAPPINGS, adapter=MovieAdapter(db, crowd_file=os.path.join(tempfile.mkdtemp(), "x.json")))
    def tense_rank(person_db):
        engine = WishmatchMovies(person_db, MOOD_MAPPINGS,
                                 adapter=MovieAdapter(person_db, crowd_file=os.path.join(tempfile.mkdtemp(), "x.json")))
        titles = [i["title"] for i in engine.recommend(media_type="movie", profile="me", page_size=24)["items"]]
        spots = [n for n, t in enumerate(titles) if t.startswith("Tense Film")]
        return (sum(spots) / len(spots) if spots else 99.0), titles

    plain, titles = tense_rank(db)
    check("without a profile the tense films sit high", plain < 8, (plain, titles[:6]))
    db.set_wishmatch_profile(1, "You avoid tension.", [], ["tense"], None, True)
    avoided, titles = tense_rank(db)
    check("with 'tense' disliked, tense films rank lower and funny films lead",
          avoided > plain + 4 and any(t.startswith("Funny Film") for t in titles[:3]), (plain, avoided, titles[:6]))
    db.set_wishmatch_profile(1, "You like tension.", ["tense"], [], None, True)
    liked, titles = tense_rank(db)
    check("with 'tense' liked, they rank higher still", liked <= plain, (plain, liked))

    print("\n[6] The HTTP routes")
    with tempfile.TemporaryDirectory() as tmp:
        cfg_file = os.path.join(tmp, "config.yaml")
        with open(cfg_file, "w", encoding="utf-8") as f:
            json.dump({
                "media_folders": [os.path.join(tmp, "Movies"), os.path.join(tmp, "Shows")],
                "jellyfin_library": {"movie_dir": os.path.join(tmp, "Movies"), "tv_dir": os.path.join(tmp, "Shows")},
                "torrent": {"download_dir": os.path.join(tmp, "downloads")},
            }, f)
        os.environ["CONFIG_PATH"] = cfg_file
        os.environ["DATABASE_PATH"] = os.path.join(tmp, "c.db")
        os.environ["MODELS_DIR"] = os.path.join(tmp, "models")
        import server  # noqa: E402 - needs the env set first
        from fastapi.testclient import TestClient
        client = TestClient(server.app)

        check("GET with no profile is 404", client.get("/api/rec/movies/profile/1").status_code == 404)
        check("an unknown domain is 404", client.get("/api/rec/music/profile/1").status_code == 404)
        r = client.put("/api/rec/movies/profile/1", json={"summary": "You like quiet films.", "likes": ["cozy", "nope"],
                                                          "dislikes": ["tense", "tense"]})
        d = r.json()
        check("a human PUT stores likes without unknown words and edited=true",
              r.status_code == 200 and d["likes"] == ["cozy"] and d["dislikes"] == ["tense"] and d["edited"] is True, d)
        r = client.put("/api/rec/movies/profile/2", json={"summary": "Pushed.", "likes": ["silly"], "model": "claude-haiku-5-5"})
        check("a push (model given) stores edited=false", r.status_code == 200 and r.json()["edited"] is False
              and r.json()["model"] == "claude-haiku-5-5", r.text)
        r = client.get("/api/rec/movies/profile/1")
        check("GET returns what was stored", r.status_code == 200 and r.json()["summary"] == "You like quiet films.", r.text)
        check("an empty summary is refused", client.put("/api/rec/movies/profile/1", json={"summary": "  "}).status_code == 422)
        check("the server's own database has it", server.db.get_wishmatch_profile(2)["likes"] == ["silly"])

    print("\n[Ollama] The default: the bake-off winner, free")
    o_db = twm.catalogue()
    o_ids = twm.ids_by_title(o_db)
    for i in range(6):
        twm.rate(o_db, 1, o_ids[f"Funny Film {i}"], 9)
        twm.rate(o_db, 1, o_ids[f"Tense Film {i}"], 2)
    fake = FakeOllama({"summary": "You like gentle comedies.", "likes": ["cozy"], "dislikes": []})
    never = FakeClient({})
    code, out = run(["--db", o_db.db_path, "--model", "gemma3:4b", "--overwrite-edited"], api=never, ollama=fake)
    prof = o_db.get_wishmatch_profile(1)
    check("without --haiku the profiles come from Ollama, no API call", code == 0 and fake.calls >= 1 and not never.calls, out)
    check("the profile records the Ollama model", prof and prof["model"] == "gemma3:4b", prof)
    check("liked and avoided words come from the ratings, not the model",
          prof and set(prof["likes"]) <= {"silly", "witty"} and prof["likes"] and "tense" in prof["dislikes"]
          and "cozy" not in prof["likes"], prof)
    check("it says it is free", "free" in out, out)

    class CutOff(FakeOllama):
        def generate(self, prompt, schema=None, system=""):
            return '{"summary": "You like silly films. You avoid tense ones. You also enjoy long'

    run(["--db", o_db.db_path, "--model", "gemma3:4b", "--overwrite-edited"], ollama=CutOff({}))
    prof = o_db.get_wishmatch_profile(1)
    check("a cut-off answer keeps its full sentences", prof["summary"] == "You like silly films. You avoid tense ones.",
          prof["summary"])

    schema_text = json.dumps(wp.output_schema())
    check("the Haiku schema uses no keywords the API refuses (maxItems, minItems)",
          "maxItems" not in schema_text and "minItems" not in schema_text, schema_text[:200])

    if failures:
        print(f"\n{len(failures)} check(s) failed")
        sys.exit(1)
    print("\nall checks passed")


if __name__ == "__main__":
    main()
