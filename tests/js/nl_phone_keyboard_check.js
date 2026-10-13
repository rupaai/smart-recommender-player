// Behaviour check for static/nl/tv/phone-keyboard.js (M8b), with a fake fetch, fake timers and a fake QR.
//   node tests/js/nl_phone_keyboard_check.js
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

let failed = 0;
function check(label, ok, detail) {
  if (ok) { console.log("  ok  " + label); } else { failed += 1; console.log("  FAIL " + label + (detail !== undefined ? " -> " + JSON.stringify(detail) : "")); }
}

const src = fs.readFileSync(path.join(__dirname, "..", "..", "static", "nl", "tv", "phone-keyboard.js"), "utf8");

function world() {
  const w = { calls: [], timers: [], listeners: {}, store: {}, now: 0 };
  w.responses = [];   // queued {status, json} for fetch, in order
  w.window = w;
  w.location = { origin: "http://192.168.1.50:8000", host: "192.168.1.50:8000" };
  w.sessionStorage = { getItem: (k) => (k in w.store ? w.store[k] : null), setItem: (k, v) => { w.store[k] = String(v); }, removeItem: (k) => { delete w.store[k]; } };
  w.setTimeout = (fn, ms) => { const id = w.timers.length + 1; w.timers.push({ id, fn, ms, live: true }); return id; };
  w.clearTimeout = (id) => { const t = w.timers.find((x) => x.id === id); if (t) { t.live = false; } };
  w.addEventListener = (name, fn) => { (w.listeners[name] = w.listeners[name] || []).push(fn); };
  w.NLQR = { toElement: (text) => ({ qrFor: text }) };
  w.fetch = (url, init) => {
    w.calls.push({ url, init: init || {} });
    const r = w.responses.shift() || { status: 500, json: {} };
    return Promise.resolve({ ok: r.status >= 200 && r.status < 300, status: r.status, json: () => Promise.resolve(r.json) });
  };
  vm.createContext(w);
  vm.runInContext(src, w);
  return w;
}
const tick = () => new Promise((r) => setImmediate(r));
async function settle() { for (let i = 0; i < 6; i++) { await tick(); } }
function fire(w) { const t = w.timers.filter((x) => x.live).pop(); if (t) { t.live = false; t.fn(); } return !!t; }
function elems() {
  return {
    panel: { hidden: true }, qr: { hidden: false, children: [], textContent: "", appendChild(c) { this.children.push(c); } },
    code: { textContent: "" }, how: { textContent: "" }
  };
}
const session = { code: "K7M2QD", key: "k".repeat(30), expires_in: 600, max_length: 100 };

(async () => {
  console.log("create and render");
  let w = world();
  let e = elems();
  const texts = [], submits = [];
  w.responses.push({ status: 200, json: session });
  w.NLPhoneKeyboard.start({ panel: e.panel, qr: e.qr, code: e.code, how: e.how, onText: (t) => texts.push(t), onSubmit: () => submits.push(1) });
  await settle();
  check("it asks for a session with POST, no key yet", w.calls[0].url === "/api/nl/type/session" && w.calls[0].init.method === "POST" && JSON.parse(w.calls[0].init.body).previous_key === null, w.calls[0]);
  check("the panel shows, the code is split in two", e.panel.hidden === false && e.code.textContent === "K7M 2QD", e.code.textContent);
  check("the QR holds this server's address with the code, and not the key", e.qr.children.length === 1 && e.qr.children[0].qrFor === "http://192.168.1.50:8000/nl/type/K7M2QD" && !JSON.stringify(e.qr.children).includes(session.key), e.qr.children);
  check("the key is kept for this tab only", w.store["nl.typeKey"] === session.key);
  check("the how-to text names the address to type", /192\.168\.1\.50:8000\/nl\/type/.test(e.how.textContent));

  console.log("polling");
  w.responses.push({ status: 200, json: { text: "alp", seq: 1, changed: true, submit_seq: 0, expires_in: 590, phone_connected: true } });
  fire(w);
  await settle();
  const p = w.calls[1];
  check("the poll sends the key as a header, not in the address", p.url === "/api/nl/type/K7M2QD/poll?after=0" && p.init.headers["X-NL-Type-Key"] === session.key && !p.url.includes(session.key), p);
  check("changed text reaches the page", texts.length === 1 && texts[0] === "alp");
  w.responses.push({ status: 200, json: { text: "alp", seq: 1, changed: false, submit_seq: 0, expires_in: 580 } });
  fire(w);
  await settle();
  check("the next poll asks after the new seq; unchanged text is not repeated", w.calls[2].url.endsWith("after=1") && texts.length === 1, w.calls[2].url);
  w.responses.push({ status: 200, json: { text: "alpha", seq: 2, changed: true, submit_seq: 1, expires_in: 570 } });
  fire(w);
  await settle();
  check("Search on the phone calls onSubmit once", texts.length === 2 && texts[1] === "alpha" && submits.length === 1);
  w.responses.push({ status: 200, json: { text: "alpha", seq: 2, changed: false, submit_seq: 1, expires_in: 560 } });
  fire(w);
  await settle();
  check("the same submit is not repeated", submits.length === 1);

  console.log("errors and renewal");
  w.responses.push({ status: 429, json: {} });
  const before = w.timers.length;
  fire(w);
  await settle();
  check("a 429 waits longer, then polls again", w.timers.length === before + 1 && w.timers[w.timers.length - 1].ms >= 3000, w.timers[w.timers.length - 1]);
  w.responses.push({ status: 404, json: {} });
  w.responses.push({ status: 200, json: Object.assign({}, session, { code: "ABCDEF", key: "z".repeat(30) }) });
  fire(w);
  await settle();
  const renew = w.calls[w.calls.length - 1];
  check("a 404 (expired, or the server restarted) makes a new session, offering the old key", renew.url === "/api/nl/type/session" && JSON.parse(renew.init.body).previous_key === session.key, renew);
  check("and the new code is shown by itself", e.code.textContent === "ABC DEF" && e.qr.children[e.qr.children.length - 1].qrFor.endsWith("/ABCDEF"));
  w.responses.push({ status: 200, json: { text: "", seq: 0, changed: false, submit_seq: 0, expires_in: 5 } });
  fire(w);
  await settle();
  check("close to expiry it renews", w.calls[w.calls.length - 1].url === "/api/nl/type/session");

  console.log("failure to start");
  w = world();
  e = elems();
  e.panel.hidden = false;
  w.responses.push({ status: 500, json: {} });
  w.NLPhoneKeyboard.start({ panel: e.panel, qr: e.qr, code: e.code, how: e.how });
  await settle();
  check("a failing server hides the panel and retries later", e.panel.hidden === true && w.timers.some((t) => t.live && t.ms >= 5000));

  console.log("no QR script");
  w = world();
  delete w.NLQR;
  e = elems();
  w.responses.push({ status: 200, json: session });
  w.NLPhoneKeyboard.start({ panel: e.panel, qr: e.qr, code: e.code, how: e.how });
  await settle();
  check("the code still shows, the square is hidden", e.panel.hidden === false && e.qr.hidden === true && e.code.textContent === "K7M 2QD" && /enter the code/.test(e.how.textContent));

  console.log("closing");
  w = world();
  e = elems();
  w.responses.push({ status: 200, json: session });
  const handle = w.NLPhoneKeyboard.start({ panel: e.panel, qr: e.qr, code: e.code, how: e.how });
  await settle();
  w.responses.push({ status: 200, json: {} });
  w.listeners.pagehide.forEach((fn) => fn());
  await settle();
  const del = w.calls[w.calls.length - 1];
  check("leaving the page sends DELETE with the key", del.init.method === "DELETE" && del.url === "/api/nl/type/K7M2QD" && del.init.headers["X-NL-Type-Key"] === session.key, del);
  check("the stored key is dropped and polling stops", !("nl.typeKey" in w.store) && !w.timers.some((t) => t.live));
  check("start returns a handle with close()", typeof handle.close === "function");

  if (failed) { console.log("\n" + failed + " FAILED"); process.exit(1); }
  console.log("\nall phone-keyboard checks passed");
})();
