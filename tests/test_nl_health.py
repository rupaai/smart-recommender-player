"""
Offline tests for Northern Lights M10b, "status and admin" (tests/test_nl_health.py): H3 the compact status in the
header (Jellyfin up, free disk, queue size) and H4 the admin screen (Jellyfin diagnostics, library repair, links to
Jellystat / Uptime Kuma / Dozzle only when configured and valid). Jellyfin and the disk are faked.

    uv run python tests/test_nl_health.py
"""

import os
import shutil
import sys
import tempfile
from collections import namedtuple

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import nl_fake  # noqa: E402
from nl_fake import FakeResponse, SECRET  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GB = 1024 ** 3
failures = []


def check(label, condition, detail=""):
    if condition:
        print(f"  ✅ {label}")
    else:
        print(f"  ❌ {label} — got: {detail}")
        failures.append(label)


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


class FakeSession:
    """Stands in for the Jellyfin requests session: public info, library list, and 204 for writes."""

    def __init__(self, movie_dir="/m", tv_dir="/t"):
        self.down = False
        self.public_status = 200
        self.folders_status = 200
        self.calls = []
        self.libs = [
            {"Name": "Movies", "CollectionType": "movies", "Locations": [movie_dir], "LibraryOptions": {"EnableRealtimeMonitor": True}},
            {"Name": "TV Shows", "CollectionType": "tvshows", "Locations": [tv_dir], "LibraryOptions": {"EnableRealtimeMonitor": False}, "ItemId": "x1"},
        ]

    def get(self, url, **kw):
        if self.down:
            raise requests.ConnectionError("down")
        self.calls.append(("GET", url))
        if url.endswith("/System/Info/Public"):
            return FakeResponse(self.public_status, json_data={"ServerName": "mars"})
        if url.endswith("/Library/VirtualFolders"):
            return FakeResponse(self.folders_status, json_data=self.libs)
        return FakeResponse(404, json_data={})

    def post(self, url, **kw):
        if self.down:
            raise requests.ConnectionError("down")
        self.calls.append(("POST", url))
        return FakeResponse(204)


def main():
    test_pure()
    tmp = tempfile.mkdtemp(prefix="nl_health_test_")
    try:
        run(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    test_static()
    if failures:
        print(f"\n{len(failures)} FAILED: {failures}")
        sys.exit(1)
    print("\nAll Northern Lights M10b (status and admin) tests passed")


def test_pure():
    from services import nl_health as nh

    print("links must be plain http(s)")
    good = ["http://mars:3000", "https://jellystat.example.com/", "http://192.168.1.50:3001/status/home", "http://localhost:8888"]
    check("plain addresses pass", all(nh.clean_http_url(u) == u for u in good), [nh.clean_http_url(u) for u in good])
    check("surrounding spaces are trimmed", nh.clean_http_url("  http://mars:3000  ") == "http://mars:3000")
    bad = ["javascript:alert(1)", "data:text/html,<script>1</script>", "file:///etc/passwd", "ftp://mars/x", "//mars/x", "mars:3000", "http://", "",
           "http://user:pass@mars:3000", "http://user@mars", "http://mars:99999", "http://mars/a b", "http://mars/\nx", "x" * 400, "HTTP://", "https://:80"]
    check("everything else is refused", all(nh.clean_http_url(u) is None for u in bad), [u for u in bad if nh.clean_http_url(u)])
    check("a non-string is refused", nh.clean_http_url(None) is None and nh.clean_http_url(12) is None and nh.clean_http_url(["http://a"]) is None)

    print("tool links come only from configuration")
    check("nothing configured: no links, nothing ignored", nh.tool_links({}, {}) == {"links": [], "ignored": []} and nh.tool_links(None, {}) == {"links": [], "ignored": []})
    cfg = {"northern_lights": {"tools": {"jellystat": "http://mars:3000", "dozzle": "http://mars:8888"}}}
    out = nh.tool_links(cfg, {})
    check("only the configured ones, in a fixed order, with names", [(l["id"], l["name"], l["url"]) for l in out["links"]]
          == [("jellystat", "Jellystat", "http://mars:3000"), ("dozzle", "Dozzle", "http://mars:8888")], out)
    out = nh.tool_links({"northern_lights": {"tools": {"uptime_kuma": "javascript:alert(1)", "dozzle": "http://u:p@x"}}}, {})
    check("an invalid one is ignored by name and its text is not echoed", out["links"] == [] and out["ignored"] == ["Uptime Kuma", "Dozzle"] and "javascript" not in str(out), out)
    out = nh.tool_links(cfg, {"NL_JELLYSTAT_URL": "https://stats.example.com"})
    check("the environment wins over the file", out["links"][0]["url"] == "https://stats.example.com")
    check("a bad tools section is harmless", nh.tool_links({"northern_lights": {"tools": "nope"}}, {}) == {"links": [], "ignored": []}
          and nh.tool_links({"northern_lights": "x"}, {}) == {"links": [], "ignored": []})

    print("Jellyfin check")

    class Svc:
        configured = True
        base_url = "http://jf.test:8096"

        def __init__(self, session):
            self.session = session

    s = FakeSession()
    check("up and the key works", nh.check_jellyfin(Svc(s)) == {"configured": True, "reachable": True, "authorised": True})
    s.folders_status = 401
    check("up but the key is refused", nh.check_jellyfin(Svc(s)) == {"configured": True, "reachable": True, "authorised": False})
    s.public_status = 503
    check("a 5xx is not reachable", nh.check_jellyfin(Svc(s))["reachable"] is False)
    s.down = True
    check("a connection error is not reachable and does not raise", nh.check_jellyfin(Svc(s))["reachable"] is False)

    class Off(Svc):
        configured = False
    check("not configured", nh.check_jellyfin(Off(FakeSession())) == {"configured": False, "reachable": False, "authorised": None})

    print("disk levels")
    Usage = namedtuple("Usage", "total used free")
    real = nh.shutil.disk_usage
    try:
        cfg = {"torrent": {"download_dir": ROOT, "min_free_gb": 20, "max_media_gb": 100}}
        nh.shutil.disk_usage = lambda p: Usage(500 * GB, 100 * GB, 400 * GB)
        d = nh.disk_summary(cfg)
        check("plenty of room is ok", d["level"] == "ok" and d["free_bytes"] == 400 * GB and d["known"] and d["min_free_gb"] == 20, d)
        nh.shutil.disk_usage = lambda p: Usage(500 * GB, 100 * GB, 39 * GB)
        check("under twice the minimum is a warning", nh.disk_summary(cfg)["level"] == "warn")
        nh.shutil.disk_usage = lambda p: Usage(500 * GB, 100 * GB, 19 * GB)
        check("under the minimum is bad (new downloads are refused)", nh.disk_summary(cfg)["level"] == "bad")
        nh.shutil.disk_usage = lambda p: Usage(500 * GB, 100 * GB, 5 * GB)
        check("with no minimum configured 10 GB is the warning line", nh.disk_summary({"torrent": {"download_dir": ROOT, "min_free_gb": 0}})["level"] == "warn")

        def boom(p):
            raise OSError("nope")
        nh.shutil.disk_usage = boom
        d = nh.disk_summary(cfg)
        check("an unreadable disk is reported as unknown, not an error", d["known"] is False and d["free_bytes"] is None)
    finally:
        nh.shutil.disk_usage = real

    print("the snapshot is cached")
    clock = [0.0]
    calls = []

    class CountingSession(FakeSession):
        def get(self, url, **kw):
            calls.append(url)
            return super().get(url, **kw)

    store = {1: {"status": "downloading", "total_size": 5, "progress": 1}, 2: {"status": "connecting"}, 3: {"status": "error", "error": "x"}, 4: {"status": "completed"}}
    # A fake, roomy disk: the real one (a CI runner has little free space) would make the level "bad".
    real_usage = nh.shutil.disk_usage
    nh.shutil.disk_usage = lambda p: namedtuple("Usage", "total used free")(500 * GB, 100 * GB, 400 * GB)
    h = nh.Health({"torrent": {"download_dir": ROOT}}, Svc(CountingSession()), store, clock=lambda: clock[0])
    first = h.status()
    n = len(calls)
    h.status()
    check("a second look inside the time reuses the Jellyfin check", len(calls) == n and n == 2, n)
    clock[0] += nh.JELLYFIN_TTL + 1
    h.status()
    check("after the time it asks again", len(calls) == n + 2)
    check("queue: downloading + queued are active, the failure is counted, finished is neither", first["queue"] == {"active": 2, "downloading": 1, "queued": 1, "importing": 0, "failed": 1}, first["queue"])
    check("a failed download makes the level a warning", first["level"] == "warn", first["level"])
    store.pop(3)
    check("without it, ok (queue is read fresh each time)", h.status()["level"] == "ok" and h.status()["queue"]["failed"] == 0)
    nh.shutil.disk_usage = real_usage


def run(tmp):
    from services import nl_health as nh

    extra = {"northern_lights": {"tools": {"jellystat": "http://mars:3000", "uptime_kuma": "javascript:alert(1)"}}}
    ctx = nl_fake.boot(tmp, extra)
    server, client, ids = ctx["server"], ctx["client"], ctx["ids"]
    state = ctx["state"]
    client.follow_redirects = False
    svc = state.jellyfin_service
    session = FakeSession(svc.movie_dir, svc.tv_dir)
    svc.session = session
    state.nl_health_state.forget()
    state.downloads_store.clear()
    state.downloads_store[1] = {"status": "downloading", "total_size": 10, "progress": 4, "download_speed": 5}
    state.downloads_store[2] = {"status": "connecting"}

    print("H3 the compact status")
    r = client.get("/api/nl/health")
    d = r.json()
    check("200 with Jellyfin, disk and queue", r.status_code == 200 and d["jellyfin"]["ok"] is True and d["disk"]["known"] and d["queue"]["active"] == 2, d)
    check("Jellyfin has a plain label", d["jellyfin"]["label"] == "Jellyfin is up")
    check("the level is one of ok / warn / bad", d["level"] in ("ok", "warn", "bad"))
    body = r.text
    check("no key, no address, no path in the reply", SECRET not in body and "jellyfin.test" not in body and "http" not in body and tmp not in body and "X-Emby" not in body, body)
    session.folders_status = 401
    state.nl_health_state.forget()
    d = client.get("/api/nl/health").json()
    check("a refused key shows as a problem with its own words", d["jellyfin"]["ok"] is False and d["level"] == "bad" and "refused" in d["jellyfin"]["label"], d["jellyfin"])
    session.folders_status = 200
    session.down = True
    state.nl_health_state.forget()
    d = client.get("/api/nl/health").json()
    check("Jellyfin down is a problem, still a 200 (the page keeps working)", d["jellyfin"]["ok"] is False and d["level"] == "bad" and "not answering" in d["jellyfin"]["label"], d)
    session.down = False
    state.nl_health_state.forget()
    check("and back up", client.get("/api/nl/health").json()["jellyfin"]["ok"] is True)

    print("H4 the admin data")
    r = client.get("/api/nl/admin")
    a = r.json()
    check("200 with health, Jellyfin, disk and tools", r.status_code == 200 and {"health", "jellyfin", "disk", "tools"} <= set(a), list(a))
    check("libraries are listed with where they point", [l["name"] for l in a["jellyfin"]["libraries"]] == ["Movies", "TV Shows"] and a["jellyfin"]["libraries"][0]["locations"] == [svc.movie_dir], a["jellyfin"])
    check("the address is host and port only", a["jellyfin"]["host"] == "jellyfin.test:8096", a["jellyfin"]["host"])
    check("settings are yes or no, never values", all(isinstance(v, bool) for v in a["jellyfin"]["settings_present"].values()), a["jellyfin"]["settings_present"])
    check("the disk carries use against the cap", "media_used_bytes" in a["disk"] and a["disk"]["max_media_gb"] > 0, a["disk"])
    check("only the valid tool link is offered; the bad one is ignored by name", [l["id"] for l in a["tools"]["links"]] == ["jellystat"] and a["tools"]["ignored"] == ["Uptime Kuma"], a["tools"])
    body = r.text
    check("no key, token, header or scheme-and-credentials anywhere", SECRET not in body and "X-Emby" not in body and "MediaBrowser" not in body and "Token" not in body and "javascript" not in body, body[:300])

    print("library repair")
    r = client.post("/api/nl/admin/repair")
    rep = r.json()
    check("the summary has a line per library and no internals", r.status_code == 200 and [l["library"] for l in rep["libraries"]] == ["Movies", "TV Shows"] and set(rep) == {"ok", "error", "libraries", "refresh_triggered"}, rep)
    check("both libraries were already attached", all(l["result"] == "already-attached" for l in rep["libraries"]) and rep["ok"] is True and rep["refresh_triggered"] is True, rep)
    check("it asked Jellyfin to rescan", ("POST", svc.base_url + "/Library/Refresh") in session.calls)
    check("the repair summary leaks nothing", SECRET not in r.text and "X-Emby" not in r.text)

    print("no tools configured")
    state.config.pop("northern_lights", None)
    a = client.get("/api/nl/admin").json()
    check("an unset tool has no link at all", a["tools"] == {"links": [], "ignored": []}, a["tools"])

    print("pages")
    page = client.get("/nl/admin")
    check("the web admin page loads", page.status_code == 200 and "nl-admin" in page.text and SECRET not in page.text)
    check("the web pages carry the status indicator", 'id="nl-health"' in client.get("/nl/").text and "health.js" in client.get("/nl/").text)
    check("the web me page links to the admin", 'href="/nl/admin"' in client.get("/nl/me").text)
    tv_pages = ["/nl/tv/", "/nl/tv/me", "/nl/tv/downloads", "/nl/tv/watchlist", "/nl/tv/choose", "/nl/tv/search", f"/nl/tv/title/{ids['Alpha']}", f"/nl/tv/show/{ids['Sigma']}"]
    texts = {p: client.get(p) for p in tv_pages}
    check("every TV page loads", all(r.status_code == 200 for r in texts.values()), {p: r.status_code for p, r in texts.items()})
    check("no TV page links to the admin", all("/nl/admin" not in r.text and "admin" not in r.text.lower() for r in texts.values()), [p for p, r in texts.items() if "admin" in r.text.lower()])
    check("the TV bar has the status as plain text", 'id="tv-health"' in texts["/nl/tv/"].text)
    check("there is no TV admin page", client.get("/nl/tv/admin").status_code == 404)
    tv_js = read("static/nl/tv/tv.js") + read("static/nl/health.js")
    check("the TV scripts never build an admin link from the TV side", '"/nl/tv/admin"' not in tv_js and "tv-health" in tv_js)


def test_static():
    print("scripts")
    health = read("static/nl/health.js")
    check("the indicator writes text only, never HTML", "innerHTML" not in health and "textContent" in health)
    check("on the TV it is not a link or focusable", "tv ? part(w.jf, null)" in health and "data-nl-focus" not in health)
    check("it polls slowly and not while the tab is hidden", "30000" in health and "document.hidden" in health)
    check("words change as well as colour", "Jellyfin down" in health and "Low disk" in health and "Disk full" in health)
    admin = read("static/nl/admin.js")
    check("the admin page writes text only", "innerHTML" not in admin)
    check("links open safely in a new tab", 'rel = "noopener noreferrer"' in admin and 'target = "_blank"' in admin)
    check("repair asks first", "window.confirm" in admin)


if __name__ == "__main__":
    main()
