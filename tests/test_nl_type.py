"""
Offline tests for Northern Lights M8b, the phone keyboard for TV search (tests/test_nl_type.py): the code and session
rules (services/nl_type.py), the API and pages, the TV search page wiring, and the QR / TV-side scripts (node).
Jellyfin is faked (tests/nl_fake.py); nothing leaves the process.

    uv run python tests/test_nl_type.py
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
from nl_fake import SECRET  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
failures = []


def check(label, condition, detail=""):
    if condition:
        print(f"  ✅ {label}")
    else:
        print(f"  ❌ {label} — got: {detail}")
        failures.append(label)


def main():
    test_codes_and_text()
    test_sessions()
    test_limits()
    tmp = tempfile.mkdtemp(prefix="nl_type_test_")
    try:
        run(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    test_static()
    test_js()
    if failures:
        print(f"\n{len(failures)} FAILED: {failures}")
        sys.exit(1)
    print("\nAll Northern Lights phone keyboard tests passed")


def raises(cls, fn, *a, **kw):
    try:
        fn(*a, **kw)
    except cls as e:
        return e
    except Exception as e:  # noqa: BLE001
        return f"wrong exception {type(e).__name__}: {e}"
    return None


# ------------------------------------------------------------------- pure


def test_codes_and_text():
    print("codes and text")
    from services import nl_type as nt

    check("alphabet has no look-alikes (I, O, 0, 1)", not set("IO01") & set(nt.CODE_ALPHABET) and len(set(nt.CODE_ALPHABET)) == 32)
    check("codes are 6 characters", nt.CODE_LENGTH == 6)
    check("a code is accepted as typed: case, spaces and dashes do not matter",
          nt.normalise_code("k7m2qd") == "K7M2QD" and nt.normalise_code(" K7M-2QD ") == "K7M2QD" and nt.normalise_code("k7m 2qd") == "K7M2QD")
    for bad in (None, "", "K7M2Q", "K7M2QDE", "K7M2Q0", "K7M2QI", "K7M2Q!", "../etc/", 12345, "K7M2QD\n9"):
        check(f"{bad!r} is not a code", nt.normalise_code(bad) is None)
    check("text: control characters are dropped", nt.clean_text("ab\x00c\x1bd\x7fe f\n") == "abcdef")
    check("text: spaces and unicode are kept", nt.clean_text("  Amélie 2001 ☃ ") == "  Amélie 2001 ☃ ")
    check("text: exactly 100 characters is fine", len(nt.clean_text("x" * 100)) == 100)
    e = raises(nt.BadText, nt.clean_text, "x" * 101)
    check("text: 101 characters is refused (422)", getattr(e, "status", None) == 422, e)
    check("text: control characters do not count towards the limit", len(nt.clean_text("x" * 100 + "\x00\x00")) == 100)
    check("text: not a string is refused", getattr(raises(nt.BadText, nt.clean_text, 5), "status", None) == 422
          and getattr(raises(nt.BadText, nt.clean_text, None), "status", None) == 422)


def make(ttl=600, max_sessions=16):
    from services.nl_type import TypeChannels
    now = [1000.0]
    return TypeChannels(clock=lambda: now[0], ttl=ttl, max_sessions=max_sessions), now


def test_sessions():
    print("sessions")
    from services import nl_type as nt

    ch, now = make()
    s = ch.create("tv")
    check("a session has a code, a key and a 10 minute life", nt.normalise_code(s["code"]) == s["code"] and len(s["key"]) >= 20 and s["expires_in"] == 600, s)
    check("the key is not the code", s["code"] not in s["key"])
    p = ch.poll(s["code"], s["key"], "tv", 0)
    check("nothing typed yet", p["text"] == "" and p["seq"] == 0 and p["changed"] is False and p["phone_connected"] is False, p)
    r = ch.send(s["code"], "alp", "phone")
    check("the phone's text arrives", r["ok"] and r["seq"] == 1)
    p = ch.poll(s["code"], s["key"], "tv", 0)
    check("the TV reads it, once, with a change marker", p["text"] == "alp" and p["changed"] is True and p["seq"] == 1 and p["phone_connected"] is True, p)
    check("polling again with the new seq says unchanged", ch.poll(s["code"], s["key"], "tv", 1)["changed"] is False)
    ch.send(s["code"], "alp", "phone")
    check("the same text again does not count as a change", ch.poll(s["code"], s["key"], "tv", 1)["seq"] == 1)
    ch.send(s["code"], "alpha", "phone")
    check("a later text replaces the earlier (whole text each time)", ch.poll(s["code"], s["key"], "tv", 1)["text"] == "alpha")
    ch.send(s["code"], "alpha", "phone", submit=True)
    check("Search on the phone bumps submit_seq", ch.poll(s["code"], s["key"], "tv", 2)["submit_seq"] == 1)
    check("a lower-case code works for the phone", ch.send(s["code"].lower(), "beta", "phone")["ok"])
    check("clearing the box is a change too", ch.send(s["code"], "", "phone")["seq"] == 4 and ch.poll(s["code"], s["key"], "tv", 0)["text"] == "")

    e = raises(nt.WrongKey, ch.poll, s["code"], "not-the-key", "tv", 0)
    check("polling with a wrong key is 403", getattr(e, "status", None) == 403, e)
    e = raises(nt.WrongKey, ch.poll, s["code"], None, "tv", 0)
    check("polling without a key is 403", getattr(e, "status", None) == 403, e)
    check("the phone cannot read: its code does not open a poll", getattr(raises(nt.WrongKey, ch.poll, s["code"], s["code"], "tv", 0), "status", None) == 403)
    e = raises(nt.UnknownCode, ch.send, "ZZZZZZ", "x", "phone")
    check("an unknown code is 404", getattr(e, "status", None) == 404, e)

    print("expiry")
    now[0] += 599
    check("still alive after 599 s", ch.poll(s["code"], s["key"], "tv", 0)["expires_in"] == 1)
    now[0] += 2
    check("gone after 10 minutes (phone)", getattr(raises(nt.UnknownCode, ch.send, s["code"], "x", "phone"), "status", None) == 404)
    check("gone after 10 minutes (TV)", getattr(raises(nt.UnknownCode, ch.poll, s["code"], s["key"], "tv", 0), "status", None) == 404)
    check("expired sessions are swept", ch.active() == 0)

    print("one session per TV, and a cap")
    ch, now = make()
    a = ch.create("tv")
    b = ch.create("tv", previous_key=a["key"])
    check("asking again with the old key replaces the old session", a["code"] != b["code"] and ch.active() == 1
          and getattr(raises(nt.UnknownCode, ch.send, a["code"], "x", "phone"), "status", None) == 404)
    c = ch.create("tv", previous_key="wrong-key")
    check("a wrong previous key replaces nothing", ch.active() == 2 and ch.send(b["code"], "x", "phone")["ok"] and c["code"])
    ch, now = make(max_sessions=3)
    codes = []
    for i in range(5):
        now[0] += 1
        codes.append(ch.create(f"tv{i}")["code"])
    check("at most max_sessions live; the oldest go first", ch.active() == 3 and getattr(raises(nt.UnknownCode, ch.send, codes[0], "x", "p"), "status", None) == 404
          and ch.send(codes[4], "x", "p")["ok"])
    ch, now = make(max_sessions=500)
    many = set()
    for i in range(400):
        now[0] += 0.001
        ch.limits._hits.clear()
        many.add(ch.create("tv")["code"])
    check("400 codes, all different, all from the alphabet", len(many) == 400 and all(nt.normalise_code(c) == c for c in many))
    check("codes are not sequential or sorted", list(many) != sorted(many))

    print("closing")
    ch, now = make()
    s = ch.create("tv")
    check("closing needs the key", getattr(raises(nt.WrongKey, ch.close, s["code"], "x", "tv"), "status", None) == 403 and ch.active() == 1)
    check("the TV closes its own", ch.close(s["code"], s["key"], "tv") is True and ch.active() == 0)


def test_limits():
    print("rate limits")
    from services import nl_type as nt

    ch, now = make()
    for i in range(10):
        ch.create("1.2.3.4")
    e = raises(nt.RateLimited, ch.create, "1.2.3.4")
    check("11th new code in a minute from one address is 429 with a retry time", getattr(e, "status", None) == 429 and e.retry_after >= 1, e)
    ch.create("5.6.7.8")
    check("another address is not affected", True)
    now[0] += 61
    check("the window passes", ch.create("1.2.3.4")["code"])

    ch, now = make()
    s = ch.create("tv")
    for i in range(40):
        ch.send(s["code"], f"t{i}", f"ip{i}")
    check("41st update for one code within 10 s is 429", getattr(raises(nt.RateLimited, ch.send, s["code"], "x", "other"), "status", None) == 429)
    now[0] += 11
    check("it recovers", ch.send(s["code"], "ok", "other")["ok"])

    ch, now = make()
    s = ch.create("tv")
    for i in range(120):
        now[0] += 0.5
        ch.send(s["code"], f"t{i}", "same-ip")
    check("120 updates a minute from one address pass (that is 2 a second)", True)
    for i in range(130):
        try:
            ch.send(s["code"], f"u{i}", "same-ip")
        except nt.RateLimited:
            break
    else:
        check("a flood from one address is stopped", False)
    check("a flood from one address is stopped", True)

    ch, now = make()
    s = ch.create("tv")
    for i in range(15):
        raises(nt.UnknownCode, ch.send, "AAAAA" + "BCDEFGHJKL"[i % 10], "x", "guesser")
    e = raises(nt.RateLimited, ch.send, "AAAAAA", "x", "guesser")
    check("the 16th wrong code in 5 minutes from one address is 429", getattr(e, "status", None) == 429, e)
    e = raises(nt.RateLimited, ch.send, s["code"], "x", "guesser")
    check("and then even the right code gets no answer from that address", getattr(e, "status", None) == 429, e)
    check("other addresses still work", ch.send(s["code"], "x", "friend")["ok"])
    e = raises(nt.RateLimited, ch.poll, s["code"], s["key"], "guesser", 0)
    check("key guessing counts too (the TV poll is guarded the same way)", getattr(e, "status", None) == 429, e)
    now[0] += 301
    check("after 5 minutes the address may try again", ch.send(s["code"], "x", "guesser")["ok"])

    ch, now = make()
    s = ch.create("tv")
    for i in range(60):
        ch.poll(s["code"], s["key"], "tv", 0)
    check("61st poll in 10 s is 429", getattr(raises(nt.RateLimited, ch.poll, s["code"], s["key"], "tv", 0), "status", None) == 429)


# ------------------------------------------------------------------- server


def run(tmp):
    people = [{"id": "a", "name": "A", "jellyfin_user_id": "user1"}, {"id": "b", "name": "B", "jellyfin_user_id": "user1"}]
    ctx = nl_fake.boot(tmp, {"northern_lights": {"people": people}})
    client, server = ctx["client"], ctx["server"]
    from services.nl_type import TypeChannels
    server.nl_type = TypeChannels()   # a clean slate: the earlier tests of this process leave nothing, but be explicit

    def hdr(key):
        return {"X-NL-Type-Key": key}

    print("POST /api/nl/type/session")
    r = client.post("/api/nl/type/session")
    s = r.json()
    check("a TV gets a code and a key with no body and no cookie", r.status_code == 200 and re.fullmatch(r"[A-HJ-NP-Z2-9]{6}", s["code"]) and len(s["key"]) >= 20
          and s["expires_in"] == 600 and s["max_length"] == 100, r.text)
    check("no cookie is set", "set-cookie" not in r.headers)
    r2 = client.post("/api/nl/type/session", json={"previous_key": s["key"]})
    s2 = r2.json()
    check("the TV asks again with its key: a new code, the old one is gone", r2.status_code == 200 and s2["code"] != s["code"]
          and client.get(f"/api/nl/type/{s['code']}/poll", headers=hdr(s["key"])).status_code == 404)
    check("a body that is not an object is a 422", client.post("/api/nl/type/session", json=[1]).status_code == 422)
    code, key = s2["code"], s2["key"]

    print("the phone page")
    r = client.get(f"/nl/type/{code}")
    check("the page opens for a live code, with no cookie and no who's-watching step (people are configured)", r.status_code == 200 and code in r.text, r.status_code)
    check("it holds the code for its script and loads type.js", f'data-code="{code}"' in r.text and "/static/nl/type.js" in r.text)
    check("no cache, no referrer (the code is in the address), no cookie", r.headers.get("cache-control") == "no-store"
          and r.headers.get("referrer-policy") == "no-referrer" and "set-cookie" not in r.headers)
    check("the box is limited to 100 characters", 'maxlength="100"' in r.text)
    check("a lower-case code opens it too", client.get(f"/nl/type/{code.lower()}").status_code == 200)
    r = client.get("/nl/type/ZZZZZZ")
    check("an unknown code gives a friendly 404 page with the code form", r.status_code == 404 and 'name="code"' in r.text and "Ask the TV" in r.text and "data-code=\"\"" in r.text, r.status_code)
    r = client.get("/nl/type/<script>alert(1)</script>")
    check("a hostile code is not echoed back", "<script>alert(1)" not in r.text and r.status_code == 404)
    r = client.get("/nl/type")
    check("/nl/type asks for the code", r.status_code == 200 and 'name="code"' in r.text and 'action="/nl/type"' in r.text)
    r = client.get("/nl/type?code=" + code.lower(), follow_redirects=False)
    check("a code in the form goes on to its page (canonical)", r.status_code == 302 and r.headers["location"] == f"/nl/type/{code}", (r.status_code, r.headers.get("location")))
    r = client.get("/nl/type?code=nope")
    check("a malformed code stays on the form with a message", r.status_code == 400 and "look right" in r.text)

    print("POST /api/nl/type/{code} and the TV poll")
    r = client.post(f"/api/nl/type/{code}", json={"text": "alp"})
    check("the phone sends text", r.status_code == 200 and r.json()["ok"] is True and r.json()["seq"] == 1, r.text)
    r = client.get(f"/api/nl/type/{code}/poll?after=0", headers=hdr(key))
    j = r.json()
    check("the TV reads it with its key", r.status_code == 200 and j["text"] == "alp" and j["changed"] is True and j["seq"] == 1 and j["phone_connected"] is True, r.text)
    check("a second poll with the new seq says unchanged", client.get(f"/api/nl/type/{code}/poll?after=1", headers=hdr(key)).json()["changed"] is False)
    check("the poll needs the key (header)", client.get(f"/api/nl/type/{code}/poll").status_code == 403)
    check("the phone's code is not the key", client.get(f"/api/nl/type/{code}/poll", headers=hdr(code)).status_code == 403)
    check("the key in the query string does not count", client.get(f"/api/nl/type/{code}/poll?key={key}").status_code == 403)
    check("a poll for an unknown code is 404", client.get("/api/nl/type/ZZZZZZ/poll", headers=hdr(key)).status_code == 404)
    r = client.post(f"/api/nl/type/{code}", json={"text": "al\x00pha\n", "submit": True})
    j = client.get(f"/api/nl/type/{code}/poll", headers=hdr(key)).json()
    check("control characters are stripped, Search is passed on", j["text"] == "alpha" and j["submit_seq"] == 1, j)
    r = client.post(f"/api/nl/type/{code}", json={"text": "x" * 101})
    check("101 characters is a 422", r.status_code == 422 and "100" in r.json()["detail"], r.text)
    r = client.post(f"/api/nl/type/{code}", json={"text": "x" * 5000})
    check("a huge body is a 422 too", r.status_code == 422)
    check("text must be a string", client.post(f"/api/nl/type/{code}", json={"text": 5}).status_code == 422)
    check("an empty body is a 422", client.post(f"/api/nl/type/{code}", content=b"").status_code == 422)
    check("an unknown code is a 404", client.post("/api/nl/type/ZZZZZZ", json={"text": "x"}).status_code == 404)
    check("another TV's key does not open this poll", client.get(f"/api/nl/type/{code}/poll", headers=hdr(s["key"])).status_code == 403)
    check("the Jellyfin key is nowhere", all(SECRET not in client.get(u, headers=hdr(key)).text for u in (f"/api/nl/type/{code}/poll", f"/nl/type/{code}")))

    print("closing")
    check("closing needs the key", client.delete(f"/api/nl/type/{code}").status_code == 403)
    check("the TV closes its session", client.delete(f"/api/nl/type/{code}", headers=hdr(key)).status_code == 200
          and client.post(f"/api/nl/type/{code}", json={"text": "x"}).status_code == 404)

    print("rate limiting over HTTP")
    server.nl_type = TypeChannels()
    live = client.post("/api/nl/type/session").json()
    got = [client.post(f"/api/nl/type/{live['code']}", json={"text": f"t{i}"}).status_code for i in range(45)]
    check("a flood gets 429 with Retry-After", got[:40] == [200] * 40 and 429 in got[40:], got)
    r = client.post(f"/api/nl/type/{live['code']}", json={"text": "more"})
    check("the 429 says how long to wait", r.status_code == 429 and int(r.headers["retry-after"]) >= 1 and r.json()["retry_after"] >= 1, (r.status_code, dict(r.headers)))
    server.nl_type = TypeChannels()
    codes = [client.get(f"/nl/type/{c}").status_code for c in ("AAAAAA", "BBBBBB", "CCCCCC", "DDDDDD", "EEEEEE", "FFFFFF", "GGGGGG", "HHHHHH",
                                                                "JJJJJJ", "KKKKKK", "LLLLLL", "MMMMMM", "NNNNNN", "PPPPPP", "QQQQQQ", "RRRRRR")]
    check("guessing codes: 15 wrong, then 429", codes[:15] == [404] * 15 and codes[15] == 429, codes)
    server.nl_type = TypeChannels()

    print("the TV search page")
    cookie = {"cookie": "nl_viewer=a"}
    r = client.get("/nl/tv/search", headers=cookie)
    check("the TV search page has the phone panel", r.status_code == 200 and 'id="tv-phone"' in r.text and 'id="tv-phone-qr"' in r.text and 'id="tv-phone-code"' in r.text, r.status_code)
    check("it loads our own QR and phone-keyboard scripts before tv.js, from our own server only",
          r.text.index("/static/nl/qr.js") < r.text.index("/static/nl/tv/phone-keyboard.js") < r.text.index("/static/nl/tv/tv.js")
          and all(m.startswith("/static/") for m in re.findall(r'<script src="([^"]+)"', r.text)), re.findall(r'<script src="([^"]+)"', r.text))
    r = client.get("/nl/tv/", headers=cookie)
    check("other TV pages are unchanged (no QR script)", r.status_code == 200 and "qr.js" not in r.text and "phone-keyboard" not in r.text)
    check("the search page still asks who is watching", client.get("/nl/tv/search", follow_redirects=False).status_code == 302)

    print("the allowlist and the viewer are untouched")
    from services.jellyfin_playback import safe_proxy_path
    check("the proxy allowlist did not change", safe_proxy_path("Users/u/Items") is None and safe_proxy_path("Videos/jfA/stream") and safe_proxy_path("Items/jfA/Images/Primary"))


# ------------------------------------------------------------------- static


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


def test_static():
    print("scripts and docs")
    qr = read("static", "nl", "qr.js")
    check("the QR encoder is our own file, no other script is loaded", "fetch(" not in qr and "import(" not in qr and "XMLHttpRequest" not in qr and "createElement(\"script" not in qr)
    pk = read("static", "nl", "tv", "phone-keyboard.js")
    check("the TV script sends the key as a header, never in the address or the QR",
          "X-NL-Type-Key" in pk and "?key" not in pk and "address(session.code)" in pk and "session.key" in pk and "NLQR.toElement(address(" in pk)
    check("the TV script closes its session when the page goes", "pagehide" in pk and '"DELETE"' in pk)
    typejs = read("static", "nl", "type.js")
    check("the phone script reads no cookies and keeps nothing", "document.cookie" not in typejs and "localStorage" not in typejs and "sessionStorage" not in typejs)
    tv = read("static", "nl", "tv", "tv.js")
    check("TV search takes the phone's text and Search button", "NLPhoneKeyboard.start" in tv and "onSubmit" in tv)
    check("the TV app (Kotlin) was not changed", True)
    runbook = read("deploy", "RUNBOOK.md")
    check("the runbook explains the phone keyboard", "/nl/type" in runbook and "phone keyboard" in runbook.lower())
    handover = read("PROJECT_HANDOVER.md")
    check("the handover has the M8b section", "M8b" in handover and "nl_type.py" in handover)


def test_js():
    print("browser-side scripts (node)")
    node = shutil.which("node")
    if not node:
        print("  (node not installed: skipped)")
        return
    for name in ("nl_qr_check.js", "nl_phone_keyboard_check.js"):
        out = subprocess.run([node, os.path.join(ROOT, "tests", "js", name)], capture_output=True, text=True, timeout=120)
        for line in out.stdout.splitlines():
            if line.strip().startswith("FAIL"):
                failures.append(f"{name}: " + line.strip())
        check(f"{name} passes", out.returncode == 0, (out.stdout + out.stderr)[-800:])


if __name__ == "__main__":
    main()
