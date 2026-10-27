// Behaviour check for the laptop app's links: tvHref in static/nl/tv/tv.js (per page root) and the player's
// page root and next-episode address (static/nl/player-extras.js). Plain node; tests/test_nl_laptop.py calls it.
//   node tests/js/nl_laptop_check.js
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

let failed = 0;
function check(label, ok, detail) {
  if (ok) { console.log("  ok  " + label); } else { failed += 1; console.log("  FAIL " + label + (detail !== undefined ? " -> " + JSON.stringify(detail) : "")); }
}
const root = path.join(__dirname, "..", "..", "static", "nl");

// tv.js runs on load; with no data-page it only defines its helpers and exposes window.NLTvHref.
function loadTv(rootAttr) {
  const body = { getAttribute: (k) => (k === "data-root" ? rootAttr : null) };
  const window = { NL: { el: () => ({}) } };
  window.window = window;
  vm.runInNewContext(fs.readFileSync(path.join(root, "tv", "tv.js"), "utf8"), { window, document: { body } });
  return window.NLTvHref;
}
const tv = loadTv("/nl/tv/");
check("tv: play link gets tv=1", tv("/nl/play/5", "/nl/tv/") === "/nl/play/5?tv=1", tv("/nl/play/5", "/nl/tv/"));
check("tv: play link with a query gets &tv=1", tv("/nl/play/5?source=2", "/nl/tv/") === "/nl/play/5?source=2&tv=1");
check("tv: title and show pages", tv("/nl/title/3", "/nl/tv/") === "/nl/tv/title/3" && tv("/nl/show/4", "/nl/tv/") === "/nl/tv/show/4");
check("tv: other links and empty ones are untouched", tv("/nl/downloads", "/nl/tv/") === "/nl/downloads" && tv("", "/nl/tv/") === "" && tv(null, "/nl/tv/") === null);
check("laptop: play link", tv("/nl/play/5", "/player/") === "/player/play/5");
check("laptop: play link keeps its query and gets no tv=1", tv("/nl/play/5?source=2", "/player/") === "/player/play/5?source=2");
check("laptop: resume query kept", tv("/nl/play/5?resume=1", "/player/") === "/player/play/5?resume=1");
check("laptop: title and show pages", tv("/nl/title/3", "/player/") === "/player/title/3" && tv("/nl/show/4", "/player/") === "/player/show/4");
check("laptop: other links untouched", tv("/nl/downloads", "/player/") === "/nl/downloads");

const X = require(path.join(root, "player-extras.js"));
check("page root: web /nl/, TV /nl/tv/ (also the Android app), laptop /player/",
  X.pageRoot("web", "/nl/", false) === "/nl/" && X.pageRoot("tv", "/nl/tv/", true) === "/nl/tv/"
  && X.pageRoot("web", null, true) === "/nl/tv/" && X.pageRoot("laptop", "/player/", true) === "/player/");
check("next episode: web unchanged", X.playUrl("web", false, "7", 12) === "/nl/play/7?source=12");
check("next episode: tv keeps tv=1", X.playUrl("tv", true, "7", 12) === "/nl/play/7?source=12&tv=1");
check("next episode: laptop is /player/play without tv=1", X.playUrl("laptop", true, "7", 12) === "/player/play/7?source=12");

if (failed) { console.log(failed + " check(s) failed"); process.exit(1); }
console.log("all laptop link checks passed");
