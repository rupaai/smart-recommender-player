"""
Offline tests: the old debug/maintenance endpoints are gone and there is no CORS.

    uv run python tests/test_removed_endpoints.py
"""

import json
import os
import shutil
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


def main():
    tmp = tempfile.mkdtemp(prefix="removed_endpoints_")
    try:
        cfg_file = os.path.join(tmp, "config.yaml")
        with open(cfg_file, "w", encoding="utf-8") as f:
            json.dump({
                "media_folders": [os.path.join(tmp, "Movies"), os.path.join(tmp, "Shows")],
                "jellyfin_library": {"movie_dir": os.path.join(tmp, "Movies"), "tv_dir": os.path.join(tmp, "Shows")},
                "torrent": {"download_dir": os.path.join(tmp, "downloads")},
            }, f)
        os.environ["CONFIG_PATH"] = cfg_file
        os.environ["DATABASE_PATH"] = os.path.join(tmp, "t.db")
        import server
        from fastapi.testclient import TestClient

        client = TestClient(server.app)
        victim = os.path.join(tmp, "Movies")
        os.makedirs(victim, exist_ok=True)

        r = client.post("/api/media/cleanup", json={"paths": [victim]})
        check("cleanup route is gone", r.status_code in (404, 405), r.status_code)
        check("media folder was not touched", os.path.isdir(victim))
        for method, path in [("get", "/api/media/files"), ("get", "/api/db/sources"),
                             ("post", "/api/db/sources/prune"), ("post", "/api/sync"),
                             ("get", "/api/jellyfin/repair")]:
            r = getattr(client, method)(path)
            check(f"{method.upper()} {path} is gone", r.status_code in (404, 405), r.status_code)

        r = client.get("/healthz", headers={"Origin": "http://evil.example"})
        check("cross-site request gets no allow-origin", "access-control-allow-origin" not in r.headers, dict(r.headers))
        check("cross-site request gets no allow-credentials", "access-control-allow-credentials" not in r.headers)
        r = client.options("/api/status", headers={"Origin": "http://evil.example",
                                                   "Access-Control-Request-Method": "POST"})
        check("preflight gets no allow headers", "access-control-allow-origin" not in r.headers
              and "access-control-allow-methods" not in r.headers, dict(r.headers))

        # Jellyfin links use the host the page was loaded from, port 8096.
        r = client.get("/api/status", headers={"Host": "tv.lan:7100"})
        check("jellyfin link uses the request host", r.json().get("jellyfin_url") == "http://tv.lan:8096", r.text)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if failures:
        print(f"\n{len(failures)} FAILED: {failures}")
        sys.exit(1)
    print("\nAll removed-endpoint tests passed")


if __name__ == "__main__":
    main()
