/* The phone keyboard page (M8b): every change to the box is sent to the TV (/api/nl/type/<code>) after a short pause;
 * the whole text goes each time, so a missed update heals itself. One request at a time; the latest text always wins.
 * No cookies and no ids of ours: the code in the address is the only credential. */
(function () {
  "use strict";

  var DEBOUNCE_MS = 220;
  var code = document.body.getAttribute("data-code");
  var input = document.getElementById("nlt-q");
  var status = document.getElementById("nlt-status");
  var form = document.getElementById("nlt-form");
  if (!code || !input) { return; }

  var timer = null;
  var busy = false;
  var again = false;         // something changed while a request was in flight
  var wantSubmit = false;
  var dead = false;
  var lastSent = null;

  function say(text, kind) {
    status.textContent = text;
    status.className = "nlt-status" + (kind ? " is-" + kind : "");
  }

  function finish(message) {
    dead = true;
    input.disabled = true;
    form.querySelector("button").disabled = true;
    say(message, "bad");
  }

  function send(submit) {
    if (dead) { return; }
    if (busy) { again = true; wantSubmit = wantSubmit || !!submit; return; }
    var text = input.value;
    if (!submit && text === lastSent) { return; }
    busy = true;
    fetch("/api/nl/type/" + encodeURIComponent(code), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: text, submit: !!submit })
    }).then(function (resp) {
      return resp.json().catch(function () { return {}; }).then(function (body) { return { status: resp.status, body: body }; });
    }).then(function (r) {
      busy = false;
      if (r.status === 200) {
        lastSent = text;
        say(text ? "Sent to the TV" : "Waiting for you to type", "ok");
      } else if (r.status === 404) {
        finish("This code has expired. Ask the TV for a new one (look at its search page).");
        return;
      } else if (r.status === 429) {
        var wait = Math.max(1, Number(r.body && r.body.retry_after) || 2);
        say("Slow down a little…", "bad");
        again = true;
        window.setTimeout(drain, wait * 1000);
        return;
      } else if (r.status === 422) {
        say((r.body && r.body.detail) || "That text can't be sent.", "bad");
      } else {
        say("Couldn't reach the server. Trying again…", "bad");
        again = true;
        window.setTimeout(drain, 2000);
        return;
      }
      drain();
    }).catch(function () {
      busy = false;
      say("Couldn't reach the server. Trying again…", "bad");
      again = true;
      window.setTimeout(drain, 2000);
    });
  }

  function drain() {
    if (!again || busy || dead) { return; }
    var submit = wantSubmit;
    again = false;
    wantSubmit = false;
    send(submit);
  }

  input.addEventListener("input", function () {
    window.clearTimeout(timer);
    timer = window.setTimeout(function () { send(false); }, DEBOUNCE_MS);
  });
  form.addEventListener("submit", function (e) {
    e.preventDefault();
    window.clearTimeout(timer);
    send(true);
  });

  say("Connected. Start typing.", "ok");   // the page itself was only served for a live code
})();
