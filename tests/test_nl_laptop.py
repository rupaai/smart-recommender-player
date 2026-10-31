"""
Offline tests for the laptop app (tests/test_nl_laptop.py): the TV pages served for a laptop browser at /player,
and the /nl/tv and /nl pages staying as they were. Jellyfin is faked (tests/nl_fake.py); nothing leaves the process.

    uv run python tests/test_nl_laptop.py
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import nl_fake  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COOKIE = {"Cookie": "nl_viewer=labiba"}
failures = []


def check(label, condition, detail=""):
    if condition:
        print(f"  ✅ {label}")
    else:
        print(f"  ❌ {label} — got: {detail}")
        failures.append(label)


def hrefs(html):
    return re.findall(r'href="([^"]*)"', html)


def run(tmp):
    ctx = nl_fake.boot(tmp)
    client, ids, server = ctx["client"], ctx["ids"], ctx["server"]
    state = ctx["state"]
    from services import nl_people as np

    alpha, show = ids["Alpha"], ids["Sigma"]
    pages = {
        "/player/": "home", "/player/search": "search", "/player/downloads": "downloads",
        f"/player/title/{alpha}": "title", f"/player/show/{show}": "show", "/player/me": "me",
        "/player/watchlist": "watchlist", "/player/choose": "choose",
    }

    print("no people configured: /player pages open straight away")
    r = client.get("/player", follow_redirects=False)
    check("/player goes to /player/", r.status_code == 302 and r.headers["location"] == "/player/", (r.status_code, r.headers.get("location")))
    r = client.get("/player/", follow_redirects=False)
    check("/player/ is 200", r.status_code == 200, r.status_code)
    r = client.get("/player/who", follow_redirects=False)
    check("/player/who goes straight to /player/", r.status_code == 302 and r.headers["location"] == "/player/", r.headers.get("location"))
    r = client.get("/player", follow_redirects=True)
    check("/player ends on the laptop home", r.status_code == 200 and 'data-surface="laptop"' in r.text)

    state.nl_people = np.People(np.parse_people([
        {"id": "labiba", "name": "Labiba", "jellyfin_user_id": "a" * 32, "profile_id": 1},
        {"id": "partner", "name": "Sam", "jellyfin_user_id": "b" * 32, "profile_id": 2},
    ]))

    print("/player pages with a viewer")
    for path, page in pages.items():
        r = client.get(path, headers=COOKIE)
        t = r.text
        check(f"{path} is 200 HTML", r.status_code == 200 and "text/html" in r.headers.get("content-type", ""), r.status_code)
        check(f"  {path}: laptop class, root and surface", '<html lang="en" class="laptop">' in t and 'data-root="/player/"' in t
              and 'data-surface="laptop"' in t and f'data-page="{page}"' in t)
        check(f"  {path}: no /nl/tv/ links", not any(h.startswith("/nl/tv") for h in hrefs(t)) and "/nl/tv/" not in t.replace("/static/nl/tv/", ""), [h for h in hrefs(t) if "/nl/" in h])
        if page != "who":
            for h in ("/player/", "/player/downloads", "/player/search", "/player/me"):
                check(f"  {path}: nav link {h}", f'href="{h}"' in t)
    r = client.get("/player/me", headers=COOKIE)
    check("me page switch link follows the root", 'href="/player/who?next=%2Fplayer%2F"' in r.text)
    r = client.get(f"/player/play/{alpha}?source=3", headers=COOKIE)
    check("player page: 200, laptop surface and root", r.status_code == 200 and 'data-surface="laptop"' in r.text
          and 'data-root="/player/"' in r.text and f'data-item-id="{alpha}"' in r.text, r.status_code)
    r = client.get(f"/player/play/{alpha}", headers=COOKIE)
    check("player page without a query is 200", r.status_code == 200)

    print("/player pages without a viewer go to /player/who")
    for path in list(pages) + [f"/player/play/{alpha}"]:
        r = client.get(path, follow_redirects=False)
        want = "/player/who?next=" + path.replace("/", "%2F")
        check(f"{path} -> who", r.status_code == 302 and r.headers["location"] == want, r.headers.get("location"))
    r = client.get(f"/player/play/{alpha}?source=7", follow_redirects=False)
    check("the player keeps its query in next", r.headers["location"] == f"/player/who?next=%2Fplayer%2Fplay%2F{alpha}%3Fsource%3D7", r.headers.get("location"))

    print("/player/who")
    r = client.get("/player/who?next=/player/search")
    check("lists the people and links to /player/who/<key>", r.status_code == 200 and "Labiba" in r.text and "Sam" in r.text
          and 'href="/player/who/labiba?next=/player/search"' in r.text and 'data-surface="laptop"' in r.text, r.text[:200])
    r = client.get("/player/who")
    check("next defaults to /player/", 'href="/player/who/labiba?next=/player/"' in r.text)
    r = client.get("/player/who?next=https://evil.test/", follow_redirects=False)
    check("an outside next is ignored", "evil.test" not in r.text)
    r = client.get("/player/who/partner", follow_redirects=False)
    check("choosing sets the cookie and goes to /player/", r.status_code == 302 and r.headers["location"] == "/player/"
          and "nl_viewer=partner" in r.headers.get("set-cookie", ""), (r.headers.get("location"), r.headers.get("set-cookie")))
    r = client.get("/player/who/partner?next=/player/search", follow_redirects=False)
    check("choosing keeps next", r.headers["location"] == "/player/search")
    r = client.get("/player/who/nobody?next=/player/search", follow_redirects=False)
    check("an unknown key goes back to /player/who", r.headers["location"].startswith("/player/who?next=") and "set-cookie" not in r.headers)

    print("/nl/tv and /nl stay as they were")
    client.cookies.clear()
    r = client.get("/nl/tv/", headers=COOKIE)
    t = r.text
    check("TV home: nav links under /nl/tv/, no laptop", 'href="/nl/tv/downloads"' in t and 'href="/nl/tv/search"' in t and 'href="/nl/tv/"' in t
          and 'class="laptop"' not in t and 'data-root="/nl/tv/"' in t and 'data-surface="tv"' in t and "/player/" not in t)
    r = client.get("/nl/tv/me", headers=COOKIE)
    check("TV me page: switch link exactly as before", 'href="/nl/tv/who?next=%2Fnl%2Ftv%2F"' in r.text)
    for path in ("/nl/tv/search", f"/nl/tv/title/{alpha}", f"/nl/tv/show/{show}", "/nl/tv/watchlist", "/nl/tv/choose", "/nl/tv/downloads"):
        r = client.get(path, headers=COOKIE)
        check(f"{path}: TV markup", r.status_code == 200 and 'class="laptop"' not in r.text and "/player/" not in r.text and 'data-surface="tv"' in r.text)
    r = client.get("/nl/tv/who")
    check("TV who page keeps its links", 'href="/nl/tv/who/labiba?next=/nl/tv/"' in r.text and 'data-root="/nl/tv/"' in r.text)
    r = client.get("/nl/tv/search", follow_redirects=False)
    check("TV redirect to its own who screen", r.headers["location"] == "/nl/tv/who?next=%2Fnl%2Ftv%2Fsearch")
    r = client.get("/nl/", follow_redirects=False)
    check("web redirect to /nl/who", r.headers["location"] == "/nl/who?next=%2Fnl%2F")
    r = client.get(f"/nl/play/{alpha}?tv=1", follow_redirects=False)
    check("/nl/play?tv=1 without a viewer goes to the TV screen", r.headers["location"].startswith("/nl/tv/who?next="))
    r = client.get("/nl/tv/who/partner", follow_redirects=False)
    check("TV choosing goes to /nl/tv/", r.headers["location"] == "/nl/tv/")
    client.cookies.clear()
    r = client.get("/nl/who/partner", follow_redirects=False)
    check("web choosing goes to /nl/", r.headers["location"] == "/nl/")
    client.cookies.clear()
    r = client.get(f"/nl/play/{alpha}?tv=1", headers=COOKIE)
    check("/nl/play?tv=1 is the tv surface", 'data-surface="tv"' in r.text and 'data-root="/nl/tv/"' in r.text)
    r = client.get(f"/nl/play/{alpha}", headers=COOKIE)
    check("/nl/play is the web surface", 'data-surface="web"' in r.text and 'data-root="/nl/"' in r.text)
    r = client.get("/nl/", headers=COOKIE)
    check("web home has no laptop markers", "laptop" not in r.text and "/player/" not in r.text)

    print("safe next")
    check("/player/ paths are allowed, others are not", np.safe_next("/player/search?q=a", "/x") == "/player/search?q=a"
          and np.safe_next("/playerx/", "/x") == "/x" and np.safe_next("//player/", "/x") == "/x")

    print("laptop look")
    css = client.get("/static/nl/tv/tv.css").text
    check("tv.css has a smaller laptop base and wheel scrolling", "html.laptop" in css and "overflow-y: auto" in css)
    check("focus.js focuses what the mouse points at, only on the laptop", "classList.contains(\"laptop\")" in client.get("/static/nl/tv/focus.js").text)


def test_js():
    print("browser-side links (node)")
    node = shutil.which("node")
    if not node:
        print("  (node not installed: skipped)")
        return
    out = subprocess.run([node, os.path.join(ROOT, "tests", "js", "nl_laptop_check.js")], capture_output=True, text=True, timeout=120)
    for line in out.stdout.splitlines():
        if line.strip().startswith("FAIL"):
            failures.append("laptop links: " + line.strip())
    check("laptop link checks pass", out.returncode == 0, (out.stdout + out.stderr)[-800:])

    print("laptop controls (node)")
    out = subprocess.run([node, os.path.join(ROOT, "tests", "js", "nl_laptop_controls_check.js")], capture_output=True, text=True, timeout=120)
    for line in out.stdout.splitlines():
        if line.strip().startswith("FAIL"):
            failures.append("laptop controls: " + line.strip())
    check("laptop controls checks pass", out.returncode == 0, (out.stdout + out.stderr)[-800:])


def main():
    tmp = tempfile.mkdtemp(prefix="nl_laptop_test_")
    try:
        run(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    test_js()
    if failures:
        print(f"\n{len(failures)} FAILED: {failures}")
        sys.exit(1)
    print("\nAll Northern Lights laptop tests passed")


if __name__ == "__main__":
    main()
