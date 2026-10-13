// Behaviour check for static/nl/polish.js (M7b): the trailer address guard, the folds, the "Unwatched only" switch,
// Hide from home, and what the details page does with the extras / "More like this" answers on the web and on the TV.
// Plain node with tiny DOM stand-ins; tests/test_nl_polish_b.py calls it when node exists.
//   node tests/js/nl_polish_b_check.js
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

let failed = 0;
function check(label, ok, detail) {
  if (ok) { console.log("  ok  " + label); } else { failed += 1; console.log("  FAIL " + label + (detail !== undefined ? " -> " + JSON.stringify(detail) : "")); }
}

function makeEl(tag) {
  const e = {
    tag, className: "", children: [], attrs: {}, listeners: {}, textContent: "", hidden: false, disabled: false, id: "", parentNode: null,
    href: "", target: "", rel: "", type: "", title: "",
    appendChild(c) { c.parentNode = e; e.children.push(c); return c; },
    insertBefore(c, ref) { c.parentNode = e; const i = ref ? e.children.indexOf(ref) : -1; if (i < 0) { e.children.push(c); } else { e.children.splice(i, 0, c); } return c; },
    get firstChild() { return e.children[0] || null; },
    setAttribute(k, v) { e.attrs[k] = String(v); },
    getAttribute(k) { return k in e.attrs ? e.attrs[k] : null; },
    hasAttribute(k) { return k in e.attrs; },
    addEventListener(t, f) { (e.listeners[t] = e.listeners[t] || []).push(f); },
    click() { (e.listeners.click || []).forEach((f) => f({ preventDefault() {}, stopPropagation() {} })); },
    classList: { toggle() {} },
    remove() { if (e.parentNode) { e.parentNode.children = e.parentNode.children.filter((c) => c !== e); } },
  };
  return e;
}
function all(node, pred, out) {
  out = out || [];
  if (pred(node)) { out.push(node); }
  (node.children || []).forEach((c) => all(c, pred, out));
  return out;
}

function load(opts) {
  const store = {};
  const requests = [];
  const answers = opts.answers || {};
  const NL = {
    el(tag, cls, text) { const n = makeEl(tag); if (cls) { n.className = cls; } if (text !== undefined && text !== null) { n.textContent = text; } return n; },
    fetchJSON(url, o) {
      requests.push({ url, method: (o && o.method) || "GET", body: o && o.body });
      const a = answers[url.split("?")[0]];
      if (a === undefined || a instanceof Error) { return Promise.reject(a || new Error("no answer")); }
      return Promise.resolve(a);
    },
    fillImage(box, sources, text) { box.textContent = text; },
    posterCard(it) { const li = makeEl("li"); li.textContent = it.title; return li; },
  };
  const sandbox = {
    window: { NL, localStorage: opts.brokenStorage ? { getItem() { throw new Error("blocked"); }, setItem() { throw new Error("blocked"); } } : { getItem: (k) => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); } }, location: { reload() {} } },
    document: { createElement: makeEl, createTextNode: (t) => ({ textContent: t }), getElementById: () => null, querySelectorAll: () => [], body: makeEl("body") },
    console, Promise, setTimeout, clearTimeout,
  };
  sandbox.window.window = sandbox.window;
  vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync(path.join(__dirname, "..", "..", "static", "nl", "polish.js"), "utf8"), sandbox);
  return { P: sandbox.window.NLPolish, store, requests, window: sandbox.window };
}
const tick = () => new Promise((r) => setTimeout(r, 5));

(async () => {
  const { P, store, requests } = load({});

  // ---- pure helpers
  check("hide label flips", P.hideLabel(false) === "Hide from home" && P.hideLabel(true) === "Show on home again");
  check("people summary counts cast and crew", P.peopleSummary({ cast: [1, 2], crew: [1] }) === "2 cast, 1 crew" && P.peopleSummary({ cast: [], crew: [1] }) === "1 crew" && P.peopleSummary({}) === "");
  check("a YouTube watch address is linkable", P.trailerHref({ url: "https://www.youtube.com/watch?v=abcDEF_123-x" }) === "https://www.youtube.com/watch?v=abcDEF_123-x");
  check("nothing else is linked",
    P.trailerHref({ url: "javascript:alert(1)" }) === null && P.trailerHref({ url: "http://www.youtube.com/watch?v=abcDEF_123" }) === null
    && P.trailerHref({ url: "https://evil.example/watch?v=abcDEF_123" }) === null && P.trailerHref({ url: "https://www.youtube.com/watch?v=abc" }) === null
    && P.trailerHref({ url: "https://www.youtube.com/watch?v=abcDEF_123&x=\"" }) === null && P.trailerHref(null) === null && P.trailerHref({}) === null);

  // ---- Unwatched only switch: remembered, reports changes, survives blocked storage
  let seen = [];
  store["nl.unwatched.movie"] = "1";
  const sw = P.unwatchedSwitch({ key: "nl.unwatched.movie", onChange: (on) => seen.push(on) });
  check("starts on from the remembered value", sw.nlOn === true && sw.attrs["aria-pressed"] === "true" && sw.textContent === "Unwatched only");
  sw.click();
  check("click turns it off, stores it and tells the page", sw.nlOn === false && sw.attrs["aria-pressed"] === "false" && store["nl.unwatched.movie"] === "0" && seen.join() === "false", seen);
  sw.click();
  check("click again turns it on", sw.nlOn === true && store["nl.unwatched.movie"] === "1" && seen.join() === "false,true");
  const tvSw = P.unwatchedSwitch({ key: "k", tv: true, onChange() {} });
  check("the TV switch is a focusable pill", tvSw.className === "tv-pill" && tvSw.hasAttribute("data-nl-focus"));
  const broken = load({ brokenStorage: true });
  const bsw = broken.P.unwatchedSwitch({ key: "k", onChange: (on) => seen.push("b" + on) });
  bsw.click();
  check("blocked storage does not break the switch", bsw.nlOn === true && seen[seen.length - 1] === "btrue");

  // ---- folds
  const body = makeEl("div");
  const f = P.fold("Cast & crew", "2 cast", body, { key: "cast" });
  const head = f.nlHead;
  const panel = f.children[1];
  check("a fold starts closed", head.attrs["aria-expanded"] === "false" && panel.hidden === true && head.attrs["aria-controls"] === panel.id);
  head.click();
  check("pressing the heading opens it and remembers", head.attrs["aria-expanded"] === "true" && panel.hidden === false && store["nl.fold.cast"] === "1");
  const f2 = P.fold("Cast & crew", "", makeEl("div"), { key: "cast" });
  check("it opens again next time", f2.nlHead.attrs["aria-expanded"] === "true");
  const tvFold = P.fold("Tech details", "", makeEl("div"), { key: "tech", tv: true });
  check("on the TV the heading is a focusable button", tvFold.nlHead.hasAttribute("data-nl-focus") && /tv-btn/.test(tvFold.nlHead.className));

  // ---- details page extras on the web
  const extras = {
    cast: [{ name: "Ann", role: "Lead", image: "/nl/jf/Items/" + "a".repeat(32) + "/Images/Primary?maxWidth=200&quality=80" }],
    crew: [{ name: "Dee", role: "Director", image: null }],
    trailer: { key: "abcDEF_123", name: "Trailer", url: "https://www.youtube.com/watch?v=abcDEF_123" },
    tech: [{ label: "Container", value: "MKV" }],
  };
  const more = { items: [{ id: 5, title: "Zeta", reason: "Similar on TMDB" }], engines: ["tmdb"] };
  let w = load({ answers: { "/api/nl/title/7/extras": extras, "/api/nl/title/7/more": more } });
  let host = makeEl("div"), actions = makeEl("div");
  w.P.titleExtras({ id: 7, title: "Alpha" }, host, actions, {});
  await tick();
  const link = actions.children[0];
  check("web: the Trailer is a link that opens a new tab without opener", link && link.tag === "a" && link.href === extras.trailer.url && link.target === "_blank" && /noopener/.test(link.rel), link && { t: link.target, r: link.rel });
  const folds = host.children[0];
  check("web: Cast & crew and Tech details folds come first, collapsed", folds && folds.className === "nl-folds" && folds.children.length === 2 && all(folds, (n) => n.attrs && n.attrs["aria-expanded"] === "false").length === 2);
  check("web: the cast list has a person and the crew list one", all(folds, (n) => n.className === "nl-person").length === 2);
  check("web: More like this row is last, with the reason line", host.children[host.children.length - 1].className.indexOf("nl-more") >= 0 && all(host, (n) => n.className === "nl-reason" && n.textContent === "Similar on TMDB").length === 1);
  check("web: only the two read requests were made", w.requests.length === 2 && w.requests.every((r) => r.method === "GET"), w.requests);

  // ---- the TV: no link, a line of text, focusable fold buttons in one row, cards from the page
  w = load({ answers: { "/api/nl/title/7/extras": extras, "/api/nl/title/7/more": more } });
  host = makeEl("div");
  const note = makeEl("p");
  note.hidden = true;
  w.P.titleExtras({ id: 7, title: "Alpha" }, host, null, { tv: true, trailerNote: note, buildCard: (it) => { const li = makeEl("li"); li.textContent = "tv:" + it.title; return li; } });
  await tick();
  check("TV: the trailer is a note, not a link", note.hidden === false && /not available on the TV/.test(note.textContent) && all(host, (n) => n.tag === "a").length === 0);
  check("TV: the folds are one row of focusable buttons", host.children[0].attrs["data-nl-row"] === "extras" && all(host.children[0], (n) => n.hasAttribute && n.hasAttribute("data-nl-focus")).length === 2);
  check("TV: More like this is its own row built with the TV card", all(host, (n) => n.attrs && n.attrs["data-nl-row"] === "more").length === 1 && all(host, (n) => n.textContent === "tv:Zeta").length === 1);

  // ---- missing parts: nothing is added and nothing throws
  w = load({ answers: { "/api/nl/title/7/extras": { cast: [], crew: [], trailer: null, tech: null }, "/api/nl/title/7/more": { items: [], engines: [] } } });
  host = makeEl("div"); actions = makeEl("div");
  w.P.titleExtras({ id: 7, title: "Alpha" }, host, actions, {});
  await tick();
  check("empty answers add nothing", host.children.length === 0 && actions.children.length === 0);
  w = load({ answers: {} });
  host = makeEl("div"); actions = makeEl("div");
  w.P.titleExtras({ id: 7, title: "Alpha" }, host, actions, {});
  await tick();
  check("failing requests add nothing and do not throw", host.children.length === 0 && actions.children.length === 0);
  w = load({ answers: { "/api/nl/title/7/extras": { cast: [], crew: [], trailer: { url: "javascript:alert(1)" }, tech: null }, "/api/nl/title/7/more": { items: [] } } });
  host = makeEl("div"); actions = makeEl("div");
  w.P.titleExtras({ id: 7, title: "Alpha" }, host, actions, {});
  await tick();
  check("a trailer address that is not YouTube's is never linked", actions.children.length === 0);

  // ---- hide toggle on a details page
  w = load({ answers: { "/api/nl/hidden/7": { hidden: true } } });
  const states = [];
  const b = w.P.hideToggle({ id: 7, title: "Alpha", hidden: false }, { onChange: (h) => states.push(h) });
  check("hide button starts as Hide from home", b.textContent === "Hide from home" && /Alpha/.test(b.attrs["aria-label"]));
  b.click();
  await tick();
  check("it sends PUT {hidden: true} and flips its label", w.requests[0].method === "PUT" && w.requests[0].body === JSON.stringify({ hidden: true }) && b.textContent === "Show on home again" && states[0] === true, w.requests);
  w = load({ answers: {} });
  const b2 = w.P.hideToggle({ id: 7, title: "Alpha", hidden: false }, { onChange: (h) => states.push(h) });
  b2.click();
  await tick();
  check("a failed request keeps the label and reports null", b2.textContent === "Hide from home" && states[states.length - 1] === null && b2.disabled === false);

  console.log(failed ? "\n" + failed + " check(s) failed" : "\nall ok");
  process.exit(failed ? 1 : 0);
})();
