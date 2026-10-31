"""
Offline tests for Northern Lights M10a, "downloads" (tests/test_nl_downloads.py): H1 download states in the new
app (queued, downloading with speed and time left, importing, ready with a Play link, failed) and H2 Retry with a
clear error. The downloader itself is faked; the store and the endpoints are the real ones.

    uv run python tests/test_nl_downloads.py
"""

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import nl_fake  # noqa: E402
from nl_fake import SECRET  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAGNET = "magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567&dn=Secret.Release.Name"
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


def main():
    test_pure()
    tmp = tempfile.mkdtemp(prefix="nl_dl_test_")
    try:
        run(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    test_static()
    if failures:
        print(f"\n{len(failures)} FAILED: {failures}")
        sys.exit(1)
    print("\nAll Northern Lights M10a (downloads) tests passed")


def test_pure():
    from services import nl_downloads as nd

    print("states")
    st = nd.state_of
    check("connecting and checking are queued", st({"status": "connecting"}) == "queued" and st({"status": "checking_files"}) == "queued"
          and st({"status": "downloading_metadata"}) == "queued" and st({}) == "queued")
    check("downloading with no size yet is still queued", st({"status": "downloading", "progress": 0, "total_size": 0}) == "queued")
    check("downloading with a size is downloading (also with libtorrent's long name)",
          st({"status": "downloading", "total_size": 10}) == "downloading" and st({"status": "torrent_status.downloading", "progress": 3}) == "downloading")
    check("organizing and seeding are importing", st({"status": "organizing"}) == "importing" and st({"status": "seeding"}) == "importing")
    check("completed is ready", st({"status": "completed"}) == "ready")
    check("error is failed, cancelled is cancelled (its error text does not make it failed)",
          st({"status": "error", "error": "x"}) == "failed" and st({"status": "cancelled", "error": "Download cancelled by user"}) == "cancelled")

    print("plain-words errors")
    fe = nd.friendly_error
    check("empty becomes a sentence", "unknown" in fe("") and "unknown" in fe(None))
    check("the disk guard's own sentence is kept", fe("Not enough room: your media folder holds 90.0 GB.").startswith("Not enough room"))
    check("a fake torrent is explained", "too small" in fe("File size too small (fake torrent link).") and "another copy" in fe("File size too small (fake torrent link)."))
    check("paths are cut out", "/srv/couchcinema/downloads" not in fe("Permission denied: '/srv/couchcinema/downloads/staging/12'") and "a file" in fe("Permission denied: '/srv/couchcinema/downloads/staging/12'"))
    check("long text is capped", len(fe("x " * 1000)) <= nd.MAX_ERROR_CHARS)
    check("a stopped download says so", fe("Download cancelled by user", "cancelled") == "You stopped this download.")

    print("one row")
    req = {"magnet": MAGNET, "title": "Clean Title", "media_type": "movie"}
    d = nd.describe(7, {"status": "downloading", "name": "Ugly.Release.1080p", "progress": 41.234, "total_size": 4000, "downloaded": 1600,
                        "download_speed": 2_500_000, "eta_seconds": 125}, req)
    check("clean title wins over the release name; numbers carry through",
          d["title"] == "Clean Title" and d["percent"] == 41.2 and d["speed_bytes"] == 2_500_000 and d["eta_seconds"] == 125 and d["state"] == "downloading", d)
    check("a running download has no Retry and cannot be dismissed", not d["can_retry"] and not d["can_dismiss"] and d["href"] is None)
    r = nd.describe(7, {"status": "error", "error": "Boom"}, req)
    check("failed: reason, Retry (we hold the link), dismiss", r["state"] == "failed" and r["error"] == "Boom" and r["can_retry"] and r["can_dismiss"], r)
    r = nd.describe(7, {"status": "error", "error": "Boom"}, None)
    check("failed with no remembered link cannot Retry", r["state"] == "failed" and not r["can_retry"])
    r = nd.describe(7, {"status": "completed"}, req, in_library=True)
    check("ready movie: Play and details links", r["state"] == "ready" and r["play_href"] == "/nl/play/7" and r["href"] == "/nl/title/7" and r["percent"] == 100.0, r)
    r = nd.describe(8, {"status": "completed"}, {**req, "media_type": "tv"}, in_library=True)
    check("ready show: the show page, no single Play", r["href"] == "/nl/show/8" and r["play_href"] is None)
    per_copy = nd.describe("copy-a", {"status": "completed", "movie_id": 7}, {**req, "movie_id": 7}, in_library=True)
    check("download row has a per-copy id but plays its catalogue title", per_copy["id"] == "copy-a" and per_copy["play_href"] == "/nl/play/7")
    r = nd.describe(9, {"status": "completed"}, req, in_library=False)
    check("completed but not in the library yet is still importing, with a note", r["state"] == "importing" and r["note"] and r["href"] is None)
    check("the magnet link is never in a row", MAGNET not in str(d) and "magnet" not in str(d))

    print("the list")
    store = {i: {"status": "completed"} for i in range(1, 12)}
    store[20] = {"status": "downloading", "total_size": 5, "progress": 1}
    store[21] = {"status": "error", "error": "x"}
    store[22] = {"status": "connecting"}
    store[23] = {"status": "organizing"}
    pure_tmp = tempfile.mkdtemp(prefix="nl_dl_pure_")
    from database import DatabaseManager
    pure_db = DatabaseManager(os.path.join(pure_tmp, "p.db"))
    out = nd.build_list(store, nd.DownloadRequests(pure_db), lambda i: None, lambda i: True)
    order = [r["state"] for r in out["downloads"]]
    check("downloading, importing, queued, failed, then ready", order[:4] == ["downloading", "importing", "queued", "failed"] and set(order[4:]) == {"ready"}, order)
    check("only the newest few finished ones are listed", order.count("ready") == nd.MAX_READY_SHOWN)
    check("counts: active = queued + downloading + importing", out["counts"]["active"] == 3 and out["counts"]["failed"] == 1, out["counts"])
    reqs = nd.DownloadRequests(pure_db)
    for i in range(nd.MAX_REQUESTS + 5):
        reqs.remember(i, "m", "t", "movie")
    check("the remembered links are bounded", reqs.get(0) is None and reqs.get(nd.MAX_REQUESTS + 4) is not None)
    shutil.rmtree(pure_tmp, ignore_errors=True)


def run(tmp):
    ctx = nl_fake.boot(tmp)
    server, client, ids = ctx["server"], ctx["client"], ctx["ids"]
    state = ctx["state"]
    store = state.downloads_store
    store.clear()
    alpha, sigma, gamma = ids["Alpha"], ids["Sigma"], ids["Gamma Ray"]
    client.follow_redirects = False

    started = []

    async def fake_download(magnet, download_id, movie_id, title, media_type="movie"):
        started.append((magnet, download_id, movie_id, title, media_type))

    state.torrent_downloader.download = fake_download

    print("starting a download remembers how to start it again")
    r = client.post("/api/torrent/download", json={
        "magnet_link": MAGNET, "movie_id": gamma, "movie_title": "Gamma.Ray.2018.1080p.WEB-DL.x264", "media_type": "movie"})
    check("the old endpoint still works", r.status_code == 200 and r.json()["success"], r.text)
    gamma_download_id = r.json()["download_id"]
    mem = state.download_requests.get(gamma_download_id)
    check("the clean title and link are remembered", mem and mem["magnet"] == MAGNET and "1080p" not in mem["title"], mem)

    print("same catalogue title, separate torrent copies")
    other_magnet = "magnet:?xt=urn:btih:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa&dn=Gamma.Ray.Other.Release"
    r2 = client.post("/api/torrent/download", json={
        "magnet_link": other_magnet, "movie_id": gamma, "movie_title": "Gamma Ray", "media_type": "movie"})
    other_download_id = r2.json().get("download_id")
    check("same movie id gets a distinct download id", r2.status_code == 200 and other_download_id and other_download_id != gamma_download_id
          and store[gamma_download_id]["movie_id"] == store[other_download_id]["movie_id"] == gamma, (r2.text, store))
    check("each release keeps its own retry magnet", state.download_requests.get(gamma_download_id)["magnet"] == MAGNET
          and state.download_requests.get(other_download_id)["magnet"] == other_magnet)
    cancelled = []

    async def fake_cancel(download_id):
        cancelled.append(download_id)
        return True

    state.torrent_downloader.cancel = fake_cancel
    cancel_response = client.post("/api/torrent/cancel", json={"download_id": gamma_download_id})
    check("cancelling one copy passes only its download id", cancel_response.json()["success"] and cancelled == [gamma_download_id])
    status = client.get(f"/api/torrent/status/{other_download_id}").json()
    check("the other copy still has its own status row", status.get("download_id") == other_download_id and status.get("movie_id") == gamma, status)

    print("the list")
    store.clear()
    gamma_key = gamma_download_id
    sigma_key, alpha_key, missing_key = "copy-sigma", "copy-alpha", "copy-missing"
    store[gamma_key] = {"download_id": gamma_key, "hash": "ab", "movie_id": gamma, "name": "Gamma.Ray.2018.1080p", "media_type": "movie", "progress": 41.5, "status": "downloading",
                    "download_speed": 3_000_000, "total_size": 4_000_000_000, "downloaded": 1_660_000_000, "eta_seconds": 780, "error": None}
    store[sigma_key] = {"download_id": sigma_key, "movie_id": sigma, "status": "connecting", "progress": 0.0, "name": "Sigma", "media_type": "tv"}
    store[alpha_key] = {"download_id": alpha_key, "movie_id": alpha, "status": "completed", "progress": 100.0, "name": "Alpha", "media_type": "movie"}
    store[missing_key] = {"download_id": missing_key, "movie_id": 99999, "status": "error", "error": "Could not open '/srv/couchcinema/downloads/x/y'"}
    state.download_requests.remember(gamma_key, MAGNET, "Gamma Ray", "movie", movie_id=gamma)
    r = client.get("/api/nl/downloads")
    d = r.json()
    rows = {row["id"]: row for row in d["downloads"]}
    check("200 with a row for each", r.status_code == 200 and set(rows) == {gamma_key, sigma_key, alpha_key, missing_key}, list(rows))
    g = rows[gamma_key]
    check("downloading: percent, speed and time left", g["state"] == "downloading" and g["percent"] == 41.5 and g["speed_bytes"] == 3_000_000 and g["eta_seconds"] == 780, g)
    check("queued", rows[sigma_key]["state"] == "queued" and rows[sigma_key]["label"] == "Waiting to start")
    check("ready movie links straight to Play", rows[alpha_key]["state"] == "ready" and rows[alpha_key]["play_href"] == f"/nl/play/{alpha}" and rows[alpha_key]["href"] == f"/nl/title/{alpha}", rows[alpha_key])
    check("failed: reason without a path; no Retry (the app was restarted since)", rows[missing_key]["state"] == "failed" and "/srv" not in rows[missing_key]["error"] and not rows[missing_key]["can_retry"], rows[missing_key])
    check("active first: downloading, queued, then failed, then ready", [r["state"] for r in d["downloads"]] == ["downloading", "queued", "failed", "ready"], [r["state"] for r in d["downloads"]])
    check("counts", d["counts"]["active"] == 2 and d["counts"]["failed"] == 1 and d["counts"]["ready"] == 1, d["counts"])
    body = r.text
    check("no magnet link, no hash, no secret, no file path in the reply", MAGNET not in body and "btih" not in body and "magnet" not in body.lower() and "0123456789abcdef" not in body
          and SECRET not in body and "ab\"" not in body and "hash" not in body and "/srv" not in body)

    print("ready waits for the library")
    with state.db.get_connection() as conn:
        conn.execute("DELETE FROM media_sources WHERE media_item_id = ?", (alpha,))
    row = {x["id"]: x for x in client.get("/api/nl/downloads").json()["downloads"]}[alpha_key]
    check("completed but with no file yet is importing, not ready", row["state"] == "importing" and row["href"] is None and row["note"], row)
    state.db.register_source_file(nl_fake.ALPHA, "Alpha", 2020, "movie")

    print("retry")
    store[gamma_key]["status"] = "error"
    store[gamma_key]["error"] = "File size too small (fake torrent link)."
    row = {x["id"]: x for x in client.get("/api/nl/downloads").json()["downloads"]}[gamma_key]
    check("a failed download we started offers Retry with a plain reason", row["state"] == "failed" and row["can_retry"] and "too small" in row["error"], row)
    check("retry of a running download is a 409", client.post(f"/api/nl/downloads/{sigma_key}/retry").status_code == 409)
    check("retry of an unknown id is a 404", client.post("/api/nl/downloads/424242/retry").status_code == 404)
    r = client.post(f"/api/nl/downloads/{missing_key}/retry")
    check("retry without a remembered link says why (404)", r.status_code == 404 and "restarted" in r.json()["detail"], r.text)
    started.clear()
    r = client.post(f"/api/nl/downloads/{gamma_key}/retry")
    check("retry starts the download again from the saved link and clean title", r.status_code == 200 and started == [(MAGNET, gamma_key, gamma, "Gamma Ray", "movie")], (r.text, started))
    check("... and the row is back to queued at once", store[gamma_key]["status"] == "connecting" and store[gamma_key]["error"] is None)
    store[gamma_key]["status"] = "cancelled"
    store[gamma_key]["error"] = "Download cancelled by user"
    row = {x["id"]: x for x in client.get("/api/nl/downloads").json()["downloads"]}[gamma_key]
    check("a stopped download offers Retry too", row["state"] == "cancelled" and row["can_retry"] and row["error"] == "You stopped this download.", row)

    print("dismiss")
    check("a running download cannot be dismissed (409)", client.delete(f"/api/nl/downloads/{sigma_key}").status_code == 409)
    check("a failed one can", client.delete(f"/api/nl/downloads/{missing_key}").status_code == 200 and missing_key not in store)
    check("dismissing twice is harmless", client.delete(f"/api/nl/downloads/{missing_key}").status_code == 200)

    print("pages")
    page = client.get("/nl/downloads")
    check("the web page loads and is in the nav", page.status_code == 200 and "nl-downloads" in page.text and 'href="/nl/downloads"' in page.text)
    tv = client.get("/nl/tv/downloads")
    check("the TV page loads and has a pill", tv.status_code == 200 and "tv-downloads" in tv.text and 'href="/nl/tv/downloads"' in client.get("/nl/tv/").text)
    check("the key is never in a page", SECRET not in page.text and SECRET not in tv.text)


def test_static():
    print("scripts")
    js = read("static/nl/downloads.js")
    check("rows are updated in place and rebuilt only on a state change", "row.state !== d.state" in js and "replaceWith" in js)
    check("it never writes HTML", "innerHTML" not in js)
    check("it polls faster while something is active", "2000" in js and "5000" in js and "document.hidden" in js)
    check("TV buttons are focusable", 'setAttribute("data-nl-focus"' in js)
    check("the TV page starts it", "NLDownloads.mount" in read("static/nl/tv/tv.js"))
    check("speed and time left are shown", "fmtSpeed" in js and "fmtEta" in js and "left" in js)


if __name__ == "__main__":
    main()
