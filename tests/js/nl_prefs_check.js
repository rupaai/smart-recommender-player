// Behaviour check for static/nl/prefs.js (M4): per-person prefs with a localStorage fallback and migration.
// Plain node with stand-ins for fetch and localStorage; tests/test_nl_people.py calls it when node exists.
//   node tests/js/nl_prefs_check.js
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

let failed = 0;
function check(label, ok, detail) {
  if (ok) { console.log("  ok  " + label); } else { failed += 1; console.log("  FAIL " + label + (detail !== undefined ? " -> " + JSON.stringify(detail) : "")); }
}

const src = fs.readFileSync(path.join(__dirname, "..", "..", "static", "nl", "prefs.js"), "utf8");

function makeEnv(server, store) {
  const calls = [];
  const localStorage = {
    getItem: (k) => (k in store ? store[k] : null),
    setItem: (k, v) => { store[k] = String(v); },
  };
  const fetch = (url, options) => {
    options = options || {};
    calls.push({ url, method: options.method, body: options.body ? JSON.parse(options.body) : null });
    if (server.down) { return Promise.reject(new Error("down")); }
    if (options.method === "PUT") { Object.assign(server.prefs, JSON.parse(options.body)); }
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ prefs: Object.assign({}, server.prefs) }) });
  };
  const window = { localStorage, fetch };
  window.window = window;
  const ctx = vm.createContext({ window, fetch, localStorage, Promise, JSON, Object, Array, console });
  vm.runInContext(src, ctx);
  return { NLPrefs: window.NLPrefs, calls, store };
}

const STYLE = { size: "L", background: "shadow", raise: 1 };
const blank = () => ({ audio_lang: null, subtitle_lang: null, subtitles_on: false, sub_style: null, search_lang: null });

(async () => {
  console.log("first load copies the device's M5 values into a person who has none");
  {
    const server = { prefs: blank() };
    const env = makeEnv(server, { "nl.subStyle": JSON.stringify(STYLE), "nl.subLang": "fra" });
    const p = await env.NLPrefs.get();
    check("prefs show the device values at once", p.sub_style && p.sub_style.size === "L" && p.search_lang === "fra", p);
    await new Promise((r) => setTimeout(r, 10));
    const put = env.calls.filter((c) => c.method === "PUT");
    check("one PUT copies both", put.length === 1 && put[0].body.search_lang === "fra" && put[0].body.sub_style.size === "L", put);
    check("the server now has them", server.prefs.search_lang === "fra" && server.prefs.sub_style.background === "shadow", server.prefs);
  }

  console.log("a person who already has prefs is not overwritten by the device's values");
  {
    const server = { prefs: Object.assign(blank(), { sub_style: { size: "S", background: "none", raise: 0 }, search_lang: "deu" }) };
    const env = makeEnv(server, { "nl.subStyle": JSON.stringify(STYLE), "nl.subLang": "fra" });
    const p = await env.NLPrefs.get();
    await new Promise((r) => setTimeout(r, 10));
    check("their own values win", p.sub_style.size === "S" && p.search_lang === "deu", p);
    check("no PUT", env.calls.every((c) => c.method !== "PUT"), env.calls);
  }

  console.log("only the missing field is copied");
  {
    const server = { prefs: Object.assign(blank(), { search_lang: "deu" }) };
    const env = makeEnv(server, { "nl.subStyle": JSON.stringify(STYLE), "nl.subLang": "fra" });
    await env.NLPrefs.get();
    await new Promise((r) => setTimeout(r, 10));
    const put = env.calls.filter((c) => c.method === "PUT");
    check("style copied, language kept", put.length === 1 && put[0].body.sub_style && !("search_lang" in put[0].body) && server.prefs.search_lang === "deu", put);
  }

  console.log("junk in localStorage is ignored");
  {
    const server = { prefs: blank() };
    const env = makeEnv(server, { "nl.subStyle": "{not json", "nl.subLang": "French" });
    const p = await env.NLPrefs.get();
    check("nothing migrated", p.sub_style === null && p.search_lang === null && env.calls.every((c) => c.method !== "PUT"), p);
  }

  console.log("save updates the cache at once and the server");
  {
    const server = { prefs: blank() };
    const env = makeEnv(server, {});
    await env.NLPrefs.get();
    const done = env.NLPrefs.save({ search_lang: "spa" });
    check("peek is updated before the server answers", env.NLPrefs.peek().search_lang === "spa");
    await done;
    check("server has it, localStorage is left alone", server.prefs.search_lang === "spa" && !("nl.subLang" in env.store), env.store);
  }

  console.log("server down: reads fall back to the device, saves are kept on the device");
  {
    const server = { prefs: blank(), down: true };
    const env = makeEnv(server, { "nl.subLang": "ita" });
    const p = await env.NLPrefs.get();
    check("fallback read", p.search_lang === "ita", p);
    await env.NLPrefs.save({ sub_style: STYLE, search_lang: "jpn" });
    check("fallback save", JSON.parse(env.store["nl.subStyle"]).size === "L" && env.store["nl.subLang"] === "jpn", env.store);
    check("peek still works", env.NLPrefs.peek().search_lang === "jpn");
  }

  console.log("localStorage that throws never breaks it");
  {
    const server = { prefs: blank() };
    const env = makeEnv(server, {});
    const bad = { getItem() { throw new Error("blocked"); }, setItem() { throw new Error("blocked"); } };
    const win = { localStorage: bad, fetch: () => Promise.reject(new Error("down")) };
    win.window = win;
    vm.runInContext(src, vm.createContext({ window: win, fetch: win.fetch, localStorage: bad, Promise, JSON, Object, Array, console }));
    const p = await win.NLPrefs.get();
    check("defaults", p.sub_style === null && p.search_lang === null, p);
    await win.NLPrefs.save({ search_lang: "eng" });
    check("save survives", win.NLPrefs.peek().search_lang === "eng");
    check("language list present", env.NLPrefs.languages.length > 20 && env.NLPrefs.languages[0][0] === "eng");
  }

  if (failed) { console.log(failed + " FAILED"); process.exit(1); }
  console.log("all prefs checks passed");
})();
