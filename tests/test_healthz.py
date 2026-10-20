"""
Offline tests for GET /healthz (tests/test_healthz.py)

Throwaway SQLite DB and models dir in a tempdir.

    uv run python tests/test_healthz.py
"""

import contextlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

failures = []


def check(label, condition, detail=""):
    if condition:
        print(f"  ✅ {label}")
    else:
        print(f"  ❌ {label} — got: {detail}")
        failures.append(label)


def user_version(path):
    conn = sqlite3.connect(path)
    try:
        return conn.execute("PRAGMA user_version;").fetchone()[0]
    finally:
        conn.close()


def test_healthz(tmp):
    print("GET /healthz")
    db_file = os.path.join(tmp, "healthz.db")
    models = os.path.join(tmp, "models")
    empty_models = os.path.join(tmp, "empty_models")
    os.makedirs(models)
    os.makedirs(empty_models)
    with open(os.path.join(models, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump({"bundle_id": "b1"}, f)

    # server.py creates its media folders on import; keep them inside the tempdir
    # (the default config.yaml points at /Users/..., which a CI runner can't create).
    cfg_file = os.path.join(tmp, "config.yaml")
    with open(cfg_file, "w", encoding="utf-8") as f:
        json.dump({
            "media_folders": [os.path.join(tmp, "Movies"), os.path.join(tmp, "Shows")],
            "jellyfin_library": {"movie_dir": os.path.join(tmp, "Movies"), "tv_dir": os.path.join(tmp, "Shows")},
            "torrent": {"download_dir": os.path.join(tmp, "downloads")},
        }, f)  # JSON is valid YAML
    os.environ["CONFIG_PATH"] = cfg_file
    os.environ["DATABASE_PATH"] = db_file
    os.environ["GIT_SHA"] = "abc1234"
    import server  # noqa: E402 - needs DATABASE_PATH set first
    from fastapi.testclient import TestClient

    client = TestClient(server.app)

    os.environ["MODELS_DIR"] = empty_models
    r = client.get("/healthz")
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    check("200 with no manifest", r.status_code == 200, r.text)
    check("ok is true", body.get("ok") is True, body)
    check("git_sha from GIT_SHA", body.get("git_sha") == "abc1234", body)
    check("db_schema matches PRAGMA user_version", body.get("db_schema") == user_version(db_file)
          and isinstance(body.get("db_schema"), int), body)
    check("model_bundle null without manifest", "model_bundle" in body and body["model_bundle"] is None, body)

    os.environ["MODELS_DIR"] = models
    body = client.get("/healthz").json()
    check("model_bundle from manifest", body.get("model_bundle") == "b1", body)

    os.environ["GIT_SHA"] = ""
    del os.environ["GIT_SHA"]
    body = client.get("/healthz").json()
    check("git_sha defaults to dev", body.get("git_sha") == "dev", body)

    with open(os.path.join(models, "manifest.json"), "w", encoding="utf-8") as f:
        f.write("{not json")
    body = client.get("/healthz").json()
    check("unreadable manifest gives null bundle", body.get("ok") is True and body.get("model_bundle") is None, body)

    real_get_connection = server.db.get_connection

    def broken_get_connection(*args, **kwargs):
        raise sqlite3.OperationalError("unable to open database file")

    server.db.get_connection = broken_get_connection
    try:
        r = client.get("/healthz")
        body = r.json()
        check("503 when SELECT 1 fails", r.status_code == 503, r.status_code)
        check("503 body is ok false with error", body.get("ok") is False and "unable to open" in body.get("error", ""), body)
    finally:
        server.db.get_connection = real_get_connection

    # A database that opens and reads fine but cannot be written must fail too.
    @contextlib.contextmanager
    def read_only_connection():
        conn = sqlite3.connect(f"file:{db_file}?mode=ro", uri=True)
        try:
            yield conn
        finally:
            conn.close()

    server.db.get_connection = read_only_connection
    try:
        r = client.get("/healthz")
        check("503 when the database is read-only", r.status_code == 503 and r.json().get("ok") is False, r.text)
    finally:
        server.db.get_connection = real_get_connection

    r = client.get("/healthz")
    check("200 again once the DB is back", r.status_code == 200 and r.json().get("ok") is True, r.text)


def main():
    tmp = tempfile.mkdtemp(prefix="healthz_test_")
    try:
        test_healthz(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if failures:
        print(f"\n{len(failures)} FAILED: {failures}")
        sys.exit(1)
    print("\nAll healthz tests passed")


if __name__ == "__main__":
    main()
