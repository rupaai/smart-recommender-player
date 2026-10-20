// Behaviour check for the Download tab in static/app.js: a result's download slot belongs to its file,
// not to its place in the list. Re-sorting or searching again used to paint a running download's
// progress, Cancel and Play onto whichever copy landed in the same position.
// Plain node with tiny DOM stand-ins; tests/test_direct_download_slots.py calls it when node exists.
//   node tests/js/direct_download_slots_check.js
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
    tag, className: "", children: [], innerHTML: "", style: {}, attrs: {},
    appendChild(c) { e.children.push(c); return c; },
    setAttribute(k, v) { e.attrs[k] = String(v); },
    addEventListener() {},
    querySelector() { return null; },
    querySelectorAll() { return []; },
  };
  return e;
}

const container = makeEl("div");
// Assigning innerHTML replaces the children, as in a browser.
Object.defineProperty(container, "innerHTML", { get() { return ""; }, set() { container.children = []; } });
const document = {
  addEventListener() {},
  getElementById(id) { return id === "directResultsContainer" ? container : null; },
  createElement: makeEl,
};
const ctx = { document, window: { addEventListener() {}, location: { hash: "" } }, console, setInterval() { return 0; }, clearInterval() {} };
ctx.globalThis = ctx;
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(path.join(__dirname, "..", "..", "static", "app.js"), "utf8") + "\n;globalThis.__state = state;", ctx);
const state = ctx.__state;

const OLD = { name: "Dune 2021 720p", magnet: "magnet:?xt=urn:btih:AAAA1111&dn=old", quality: "720p", seeds: 400, size_bytes: 1e9, score: 0.9 };
const NEW = { name: "Dune 2021 2160p", magnet: "magnet:?xt=urn:btih:BBBB2222&dn=new", quality: "2160p", seeds: 90, size_bytes: 8e9, score: 0.7 };

// The cards drawn by the last render, as { slot, magnet, html } in list order.
function cards() {
  return container.children.filter((c) => c.className === "torrent-card").map((c) => {
    const slot = /id="([^"]+)"/.exec(c.innerHTML.split('class="torrent-action-row"')[1] || "");
    const mag = /startDirectDownload\(&quot;([^&]+)&quot;/.exec(c.innerHTML);
    return { slot: slot && slot[1], magnet: mag && decodeURIComponent(mag[1]), html: c.innerHTML, name: /torrent-name">([^<]*)</.exec(c.innerHTML)[1] };
  });
}

// 1. The slot id comes from the file, so it survives a re-order.
ctx.renderDirectResults([OLD, NEW], "Dune");
const before = cards();
ctx.renderDirectResults([NEW, OLD], "Dune");
const after = cards();
check("slot ids are taken from the info hash", before[0].slot === "direct-dl-aaaa1111" && before[1].slot === "direct-dl-bbbb2222", before.map((c) => c.slot));
check("each file keeps its slot after a re-sort", after[0].slot === before[1].slot && after[1].slot === before[0].slot, after.map((c) => c.slot));
check("every Download button starts its own card's file", after.every((c) => c.magnet === null || c.magnet === (c.name === NEW.name ? NEW.magnet : OLD.magnet)), after.map((c) => [c.name, c.magnet]));

// 2. Two releases can share a catalogue movie id and still keep separate progress slots.
state.activeDownloads["copy-720"] = { downloadId: "copy-720", movieId: 7, title: "Dune", progress: 20, status: "downloading", containerId: ctx.downloadSlotId(OLD.magnet) };
state.activeDownloads["copy-4k"] = { downloadId: "copy-4k", movieId: 7, title: "Dune", progress: 40, status: "downloading", containerId: ctx.downloadSlotId(NEW.magnet) };
ctx.renderDirectResults([OLD, NEW], "Dune");
let drawn = cards();
check("both releases with the same movie id show their own active download", drawn[0].html.includes("Downloading") && drawn[1].html.includes("Downloading") && drawn.every((c) => c.magnet === null), drawn.map((c) => c.html));

// 3. Finishing one release does not mark the other release ready.
state.activeDownloads["copy-4k"].progress = 100;
ctx.renderDirectResults([NEW, OLD], "Dune");
drawn = cards();
check("Ready stays on the finished copy while the other still downloads", drawn[0].name === NEW.name && drawn[0].html.includes("Ready") && drawn[1].html.includes("Downloading"), drawn.map((c) => c.html));

// 4. The same torrent listed twice gives one card, and magnets without an info hash still get a stable slot.
delete state.activeDownloads["copy-720"];
delete state.activeDownloads["copy-4k"];
ctx.renderDirectResults([OLD, { ...OLD, name: "Dune dup" }, NEW], "Dune");
check("a torrent listed twice is drawn once", cards().length === 2, cards().map((c) => c.name));
const odd = "https://example.org/file.torrent";
check("links without an info hash get a stable slot", ctx.downloadSlotId(odd) === ctx.downloadSlotId(odd) && ctx.downloadSlotId(odd) !== ctx.downloadSlotId(odd + "2"));

if (failed) { console.log(failed + " check(s) failed"); process.exit(1); }
console.log("all direct download slot checks passed");
