/* TV search: type on your phone (M8b).
 *
 * Asks the server for a short-lived typing code, shows it with a QR code of <this server>/nl/type/<code>, and polls for
 * what the phone types. The TV page holds a session key (never shown, never in the QR) that only it can poll with.
 * When the code expires, or the server forgets it (restart), a new one is made and shown by itself.
 *
 *   NLPhoneKeyboard.start({ onText(text), onSubmit(), panel, qr, code, how })
 */
(function (global) {
  "use strict";

  var POLL_MS = 900;
  var RETRY_MS = 8000;
  var KEY_STORE = "nl.typeKey";

  function lsGet(k) { try { return global.sessionStorage.getItem(k); } catch (e) { return null; } }
  function lsSet(k, v) { try { global.sessionStorage.setItem(k, v); } catch (e) { /* private mode */ } }
  function lsDel(k) { try { global.sessionStorage.removeItem(k); } catch (e) { /* ignore */ } }

  function start(opts) {
    var session = null;
    var after = 0;
    var submitSeen = 0;
    var timer = null;
    var stopped = false;

    function later(fn, ms) {
      global.clearTimeout(timer);
      timer = global.setTimeout(fn, ms);
    }

    function address(code) { return global.location.origin + "/nl/type/" + code; }

    function render() {
      if (!opts.panel) { return; }
      var ok = false;
      try {
        opts.qr.textContent = "";
        opts.qr.appendChild(global.NLQR.toElement(address(session.code), { dark: "#14121f", light: "#ffffff", margin: 2 }));
        ok = true;
      } catch (e) { /* too long for the encoder, or no QR script: the code still works */ }
      opts.qr.hidden = !ok;
      opts.code.textContent = session.code.slice(0, 3) + " " + session.code.slice(3);
      opts.how.textContent = ok
        ? "Scan the square, or open " + global.location.host + "/nl/type and enter the code."
        : "Open " + global.location.host + "/nl/type on your phone and enter the code.";
      opts.panel.hidden = false;
    }

    function create() {
      if (stopped) { return; }
      var previous = (session && session.key) || lsGet(KEY_STORE);
      fetch("/api/nl/type/session", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ previous_key: previous || null })
      }).then(function (resp) {
        if (!resp.ok) { throw new Error("status " + resp.status); }
        return resp.json();
      }).then(function (s) {
        session = s;
        after = 0;
        submitSeen = 0;
        lsSet(KEY_STORE, s.key);
        render();
        later(poll, POLL_MS);
      }).catch(function () {
        if (opts.panel) { opts.panel.hidden = true; }
        later(create, RETRY_MS);
      });
    }

    function poll() {
      if (stopped || !session) { return; }
      fetch("/api/nl/type/" + encodeURIComponent(session.code) + "/poll?after=" + after, {
        headers: { "X-NL-Type-Key": session.key }
      }).then(function (resp) {
        if (resp.status === 404 || resp.status === 403) { session = null; create(); return null; }
        if (resp.status === 429) { later(poll, 3000); return null; }
        if (!resp.ok) { throw new Error("status " + resp.status); }
        return resp.json();
      }).then(function (j) {
        if (!j) { return; }
        if (j.changed) {
          after = j.seq;
          if (opts.onText) { opts.onText(j.text); }
        }
        if (j.submit_seq > submitSeen) {
          submitSeen = j.submit_seq;
          if (opts.onSubmit) { opts.onSubmit(); }
        }
        if (j.expires_in < 15) { create(); return; }   // renew just before it runs out
        later(poll, POLL_MS);
      }).catch(function () { later(poll, RETRY_MS); });
    }

    function close() {
      stopped = true;
      global.clearTimeout(timer);
      if (session) {
        try {
          fetch("/api/nl/type/" + encodeURIComponent(session.code), { method: "DELETE", headers: { "X-NL-Type-Key": session.key }, keepalive: true })
            .catch(function () { /* it expires by itself */ });
        } catch (e) { /* ignore */ }
        lsDel(KEY_STORE);
      }
    }
    global.addEventListener("pagehide", close);

    create();
    return { close: close };
  }

  global.NLPhoneKeyboard = { start: start };
})(window);
