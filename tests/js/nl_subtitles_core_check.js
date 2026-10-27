// Behaviour check for the web subtitle delay and look in static/nl/player-core.js (M5).
// Runs in plain node with tiny stand-ins for the DOM; tests/test_nl_subtitles.py calls it when node exists.
//   node tests/js/nl_subtitles_core_check.js
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

let failed = 0;
function check(label, ok, detail) {
  if (ok) { console.log("  ok  " + label); } else { failed += 1; console.log("  FAIL " + label + (detail !== undefined ? " -> " + JSON.stringify(detail) : "")); }
}

function makeCue(start, end) { return { startTime: start, endTime: end, line: "auto", snapToLines: false }; }

function makeEnv() {
  const styleEls = [];
  const listenersByEl = new Map();
  function makeEl(tag) {
    const el = { tag, children: [], attrs: {}, parentNode: null, textContent: "", listeners: {} };
    el.setAttribute = (k, v) => { el.attrs[k] = v; };
    el.addEventListener = (n, fn) => { (el.listeners[n] = el.listeners[n] || []).push(fn); };
    el.removeEventListener = () => {};
    el.appendChild = (c) => { el.children.push(c); c.parentNode = el; return c; };
    el.removeChild = (c) => { el.children = el.children.filter((x) => x !== c); c.parentNode = null; };
    if (tag === "track") { el.track = { mode: "disabled", cues: null }; }
    if (tag === "style") { styleEls.push(el); }
    return el;
  }
  const video = makeEl("video");
  video.canPlayType = () => "";
  video.load = () => {};
  video.removeAttribute = () => {};
  const document = { createElement: makeEl, head: makeEl("head"), addEventListener() {}, removeEventListener() {} };
  const window = { document, location: { origin: "http://x" } };
  window.window = window;
  return { window, document, video, styleEls };
}

const src = fs.readFileSync(path.join(__dirname, "..", "..", "static", "nl", "player-core.js"), "utf8");
const env = makeEnv();
const ctx = vm.createContext(Object.assign({ console }, env.window, { document: env.document }));
ctx.window = ctx;
vm.runInContext(src, ctx);
const core = ctx.NLPlayerCore;
const p = core.create(env.video);

check("web player can delay and style", p.canDelaySubtitles === true && p.canStyleSubtitles === true);

p.setTextTracks([{ id: 3, label: "English", language: "eng", url: "/x.vtt" }, { id: 4, label: "Spanish", language: "spa", url: "/y.vtt" }]);
const [a, b] = env.video.children;
check("two <track> elements", env.video.children.length === 2 && a.tag === "track");

p.showTextTrack(3);
check("the shown track is switched on, the other off", a.track.mode === "showing" && b.track.mode === "disabled");

// cues arrive later (after the track loads)
a.track.cues = [makeCue(10, 12), makeCue(20, 22.5)];
p.setSubtitleDelay(0.75);
check("delay shifts start and end", a.track.cues[0].startTime === 10.75 && a.track.cues[0].endTime === 12.75 && a.track.cues[1].endTime === 23.25, a.track.cues);
check("subtitleDelay reads back", p.subtitleDelay === 0.75);
p.setSubtitleDelay(0.5);
p.setSubtitleDelay(1.0);
p.setSubtitleDelay(0.75);
check("repeated changes don't drift", a.track.cues[0].startTime === 10.75 && a.track.cues[1].startTime === 20.75, a.track.cues);
p.setSubtitleDelay(-0.25);
check("negative delay moves earlier", a.track.cues[0].startTime === 9.75 && a.track.cues[0].endTime === 11.75, a.track.cues[0]);
p.setSubtitleDelay(-30);
check("never before zero", a.track.cues[0].startTime === 0 && a.track.cues[0].endTime === 0, a.track.cues[0]);
p.setSubtitleDelay(0);
check("back to zero restores the originals", a.track.cues[0].startTime === 10 && a.track.cues[0].endTime === 12 && a.track.cues[1].startTime === 20, a.track.cues);

// the 'load' event of a track shifts cues that were not there yet
p.setSubtitleDelay(0.25);
b.track.cues = [makeCue(5, 6)];
p.showTextTrack(4);
check("switching track applies the current delay to the new track", b.track.cues[0].startTime === 5.25 && b.track.cues[0].endTime === 6.25, b.track.cues[0]);
const c2 = makeCue(8, 9);
b.track.cues.push(c2);
(b.listeners.load || []).forEach((fn) => fn());
check("cues that arrive on 'load' are shifted too", c2.startTime === 8.25 && c2.endTime === 9.25, c2);
const aCuesBefore = a.track.cues[0].startTime;
(a.listeners.load || []).forEach((fn) => fn());
check("a load event of a hidden track changes nothing", a.track.cues[0].startTime === aCuesBefore);

// look
p.setSubtitleStyle({ size: "XL", background: "shadow", raise: 1 });
check("a ::cue rule is generated with the size and the shadow",
  env.styleEls.length === 1 && /video::cue/.test(env.styleEls[0].textContent) && /200%/.test(env.styleEls[0].textContent)
  && /text-shadow: 0 0 3px/.test(env.styleEls[0].textContent) && /transparent/.test(env.styleEls[0].textContent), env.styleEls.map((s) => s.textContent));
check("raised puts shown cues 3 lines up from the bottom", b.track.cues.every((c) => c.line === -3 && c.snapToLines === true), b.track.cues);
p.setSubtitleStyle({ size: "S", background: "box", raise: 2 });
check("the same style element is reused; box has a dark background", env.styleEls.length === 1 && /rgba\(0, 0, 0, 0\.72\)/.test(env.styleEls[0].textContent) && /90%/.test(env.styleEls[0].textContent));
check("high is 6 lines", b.track.cues.every((c) => c.line === -6));
p.setSubtitleStyle({ size: "M", background: "none", raise: 0 });
check("normal position goes back to auto", b.track.cues.every((c) => c.line === "auto"), b.track.cues);
check("none: no background and no shadow", /background-color: transparent; text-shadow: none;/.test(env.styleEls[0].textContent));
p.setSubtitleStyle({ size: "bogus", background: "bogus", raise: 99 });
check("bad values fall back to M, box and the highest line", /115%/.test(env.styleEls[0].textContent) && b.track.cues.every((c) => c.line === -6));

// the TV adapter
function nativeCore(version) {
  const calls = [];
  const e2 = makeEnv();
  const w = Object.assign({ console }, e2.window, { document: e2.document });
  w.window = w;
  w.NorthernLightsNative = {
    version: () => version, play() {}, pause() {}, resume() {}, seekTo() {}, stop() {},
    setSubtitle() {}, setSubtitleStyle: (j) => calls.push(j), setSubtitleDelay: (d) => calls.push("delay " + d)
  };
  const c = vm.createContext(w);
  vm.runInContext(src, c);
  return { player: c.NLPlayerCore.create(e2.video), calls, core: c.NLPlayerCore };
}
const v3 = nativeCore("3");
check("v3 TV: styling yes, delay no", v3.player.canStyleSubtitles === true && v3.player.canDelaySubtitles === false && v3.player.nativeVersion === 3);
v3.player.setSubtitleStyle({ size: "L", background: "box", raise: 1 });
check("v3 TV: the style goes to the bridge as JSON", v3.calls.length === 1 && JSON.parse(v3.calls[0]).size === "L", v3.calls);
v3.player.setSubtitleDelay(1);
check("v3 TV: setSubtitleDelay is a harmless no-op", v3.player.subtitleDelay === 0 && v3.calls.length === 1);
const v7 = nativeCore("7");
check("v7 TV: styling and delay", v7.player.canStyleSubtitles === true && v7.player.canDelaySubtitles === true);
v7.player.setSubtitleDelay(-1.25);
check("v7 TV: the delay goes to the bridge as a string", v7.calls.length === 1 && v7.calls[0] === "delay -1.25" && v7.player.subtitleDelay === -1.25, v7.calls);
v7.player.setSubtitleDelay(NaN);
check("v7 TV: an unusable delay is sent as 0", v7.calls[1] === "delay 0" && v7.player.subtitleDelay === 0, v7.calls);
const v2 = nativeCore("2");
v2.player.setSubtitleStyle({ size: "L" });
check("v2 TV app: no style call, no style or delay controls", v2.calls.length === 0 && v2.player.canStyleSubtitles === false && v2.player.canDelaySubtitles === false);

// the delay label in the player ("+0.75 s")
const playerSrc = fs.readFileSync(path.join(__dirname, "..", "..", "static", "nl", "player.js"), "utf8");
const m = playerSrc.match(/function formatDelay\(seconds\) \{[\s\S]*?\n  \}\n/);
check("player.js has formatDelay", !!m);
if (m) {
  const fmt = vm.runInNewContext("(" + m[0].replace(/^function formatDelay/, "function") + ")");
  check("0.75 reads +0.75 s", fmt(0.75) === "+0.75 s", fmt(0.75));
  check("-0.25 reads −0.25 s", fmt(-0.25) === "−0.25 s", fmt(-0.25));
  check("1 reads +1.00 s", fmt(1) === "+1.00 s", fmt(1));
  check("0 reads 0 s", fmt(0) === "0 s" && fmt(0.001) === "0 s", fmt(0));
  check("3 x 0.25 reads +0.75 s", fmt(0.25 + 0.25 + 0.25) === "+0.75 s");
}

if (failed) { console.log(failed + " FAILED"); process.exit(1); }
console.log("all subtitle core checks passed");
