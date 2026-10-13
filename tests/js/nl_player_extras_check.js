// Behaviour check for the player extras (M7a): static/nl/player-extras.js and the speed / volume parts of
// static/nl/player-core.js. Plain node with tiny DOM stand-ins; tests/test_nl_player_extras.py calls it when node exists.
//   node tests/js/nl_player_extras_check.js
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

let failed = 0;
function check(label, ok, detail) {
  if (ok) { console.log("  ok  " + label); } else { failed += 1; console.log("  FAIL " + label + (detail !== undefined ? " -> " + JSON.stringify(detail) : "")); }
}
const root = path.join(__dirname, "..", "..", "static", "nl");
const X = require(path.join(root, "player-extras.js"));

// ---- thumbnails: 10 s interval, 160x90 thumbs, 5x4 sheets (20 per sheet), 100 thumbnails
const trick = { width: 160, height: 90, tile_width: 5, tile_height: 4, count: 100, interval_seconds: 10, url: "/nl/jf/Videos/jf1/Trickplay/160/{index}.jpg" };
let t = X.tileFor(trick, 0);
check("second 0 is the first thumbnail of sheet 0", t.sheet === 0 && t.x === 0 && t.y === 0 && t.url.endsWith("/0.jpg"), t);
t = X.tileFor(trick, 47);
check("second 47 is thumbnail 4: column 4, row 0", t.sheet === 0 && t.x === 640 && t.y === 0, t);
t = X.tileFor(trick, 52);
check("second 52 is thumbnail 5: column 0, row 1", t.x === 0 && t.y === 90, t);
t = X.tileFor(trick, 200);
check("second 200 is thumbnail 20: sheet 1, top left, url filled in", t.sheet === 1 && t.x === 0 && t.y === 0 && t.url === "/nl/jf/Videos/jf1/Trickplay/160/1.jpg", t);
t = X.tileFor(trick, 99999);
check("past the end clamps to the last thumbnail (99: sheet 4, column 4, row 3)", t.sheet === 4 && t.x === 640 && t.y === 270, t);
check("negative time clamps to the first", X.tileFor(trick, -5).sheet === 0 && X.tileFor(trick, -5).x === 0);
check("sheet size is tiles x thumbnail", t.sheetWidth === 800 && t.sheetHeight === 360, t);
check("no trickplay, no tile", X.tileFor(null, 5) === null && X.tileFor({}, 5) === null);

// ---- chapters
const chapters = [{ start_seconds: 0, name: "Opening" }, { start_seconds: 600, name: "Middle" }, { start_seconds: 1800, name: "End" }];
check("chapter at 0 and 599.9 is the first, 600 the second", X.chapterIndexAt(chapters, 0) === 0 && X.chapterIndexAt(chapters, 599.9) === 0 && X.chapterIndexAt(chapters, 600) === 1);
check("past the last start is the last chapter", X.chapterIndexAt(chapters, 99999) === 2);
check("no chapters, no index", X.chapterIndexAt([], 5) === -1);
const ticks = X.chapterTicks(chapters, 3600);
check("ticks skip the one at the start and are fractions", ticks.length === 2 && ticks[0] === 600 / 3600 && ticks[1] === 0.5, ticks);
check("a chapter in the last second makes no tick, and no duration none", X.chapterTicks([{ start_seconds: 3599.5 }], 3600).length === 0 && X.chapterTicks(chapters, 0).length === 0);

// ---- segments
const segs = [{ type: "intro", start_seconds: 30, end_seconds: 90 }, { type: "outro", start_seconds: 2700, end_seconds: 2800 }];
check("inside the intro", X.segmentAt(segs, "intro", 45) === segs[0]);
check("a moment before the intro counts (button on time)", X.segmentAt(segs, "intro", 29.9) === segs[0] && X.segmentAt(segs, "intro", 29) === null);
check("the last moments of the intro do not count", X.segmentAt(segs, "intro", 89.5) === null && X.segmentAt(segs, "intro", 89) === segs[0]);
check("types are separate", X.segmentAt(segs, "outro", 45) === null && X.segmentAt(segs, "outro", 2750) === segs[1]);
check("no segments, nothing", X.segmentAt([], "intro", 45) === null && X.segmentAt(null, "intro", 45) === null);

// ---- speed
check("the five speeds", JSON.stringify(X.SPEEDS) === "[0.75,1,1.25,1.5,2]");
check("labels", X.speedLabel(1) === "1×" && X.speedLabel(0.75) === "0.75×" && X.speedLabel(2) === "2×");
check("stepping up and down stops at the ends", X.nextSpeed(1, 1) === 1.25 && X.nextSpeed(2, 1) === 2 && X.nextSpeed(0.75, -1) === 0.75 && X.nextSpeed(1, -1) === 0.75);
check("an odd current speed steps from 1", X.nextSpeed(3, 1) === 1.25);

// ---- gestures
check("left of the middle is the left half", X.halfOf(10, 400) === "left" && X.halfOf(199, 400) === "left" && X.halfOf(200, 400) === "right");
check("first tap waits", X.tapAction(null, 1000, "left") === "first");
check("a second tap within 300 ms on the same half is a double tap", X.tapAction({ at: 1000, half: "left" }, 1250, "left") === "double-left" && X.tapAction({ at: 1000, half: "right" }, 1100, "right") === "double-right");
check("too slow, or the other half, waits again", X.tapAction({ at: 1000, half: "left" }, 1400, "left") === "first" && X.tapAction({ at: 1000, half: "left" }, 1100, "right") === "first");
check("swiping up 240 px on an 800 px screen (half of the 480 px swipe range) adds 50%", X.swipeVolume(0.2, 600, 360, 800) === 0.7, X.swipeVolume(0.2, 600, 360, 800));
check("a full swipe is 100%, and it never leaves 0..1", X.swipeVolume(0.5, 800, 0, 800) === 1 && X.swipeVolume(0.5, 0, 800, 800) === 0);
check("swipe volume: 0.6 of the height up from 0 gives 1", X.swipeVolume(0, 700, 700 - 480, 800) === 1);
check("vertical swipes are told from taps and sideways drags", X.isVerticalSwipe(2, -40) && !X.isVerticalSwipe(40, -42) && !X.isVerticalSwipe(1, -5));

// ---- the adapters (speed, volume)
const core = fs.readFileSync(path.join(root, "player-core.js"), "utf8");
function makeEl(tag) {
  const el = { tag, children: [], attrs: {}, parentNode: null, textContent: "", listeners: {} };
  el.setAttribute = (k, v) => { el.attrs[k] = v; };
  el.addEventListener = (n, fn) => { (el.listeners[n] = el.listeners[n] || []).push(fn); };
  el.removeEventListener = () => {};
  el.appendChild = (c) => { el.children.push(c); c.parentNode = el; return c; };
  el.removeChild = (c) => { el.children = el.children.filter((x) => x !== c); c.parentNode = null; };
  return el;
}
function envWith(nativeVersion, stickyVolume) {
  const video = makeEl("video");
  video.canPlayType = () => "";
  video.load = () => {};
  video.removeAttribute = () => {};
  video.playbackRate = 1;
  video.defaultPlaybackRate = 1;
  let vol = 1;
  Object.defineProperty(video, "volume", { get: () => vol, set: (v) => { if (stickyVolume) { vol = v; } } });
  const document = { createElement: makeEl, head: makeEl("head"), addEventListener() {}, removeEventListener() {} };
  const w = { console, document, location: { origin: "http://x" }, setInterval: () => 1, clearInterval() {}, setTimeout, clearTimeout };
  w.window = w;
  const calls = [];
  if (nativeVersion) {
    w.NorthernLightsNative = {
      version: () => String(nativeVersion), play(j) { calls.push(["play", j]); }, pause() {}, resume() {}, seekTo() {}, stop() {},
      setSubtitle() {}, setSubtitleStyle() {}, setSpeed: (r) => calls.push(["setSpeed", r])
    };
  }
  const c = vm.createContext(w);
  vm.runInContext(core, c);
  return { player: c.NLPlayerCore.create(video), video, calls, window: c };
}

let e = envWith(0, true);
check("web: speed can change", e.player.canChangeSpeed === true);
e.player.playbackRate = 1.5;
check("web: the video element takes the rate, and keeps it as default for the next stream", e.video.playbackRate === 1.5 && e.video.defaultPlaybackRate === 1.5);
e.player.playbackRate = 40;
check("web: a silly rate is refused", e.video.playbackRate === 1.5);
check("web: volume can be set where the probe sticks, and the volume is put back", e.player.canSetVolume === true && e.player.volume === 1);
e = envWith(0, false);
check("web: iPhone-style (volume ignored): no volume gesture", e.player.canSetVolume === false);

e = envWith(4, true);
check("TV v4: speed can change and the version is 4", e.player.canChangeSpeed === true && e.player.nativeVersion === 4);
e.player.playbackRate = 1.25;
check("TV v4 before a stream: remembered, nothing sent", e.calls.length === 0 && e.player.playbackRate === 1.25);
e.player.load({ url: "/nl/jf/Videos/a/stream", kind: "file", startAt: 0, native: {} });
e.window.NLNativeEvents.dispatch(JSON.stringify({ type: "started", positionSeconds: 0, durationSeconds: 100, playing: true }));
check("TV v4: after the stream starts, the speed is sent again (a new native stream starts at 1x)", e.calls.some((c) => c[0] === "setSpeed" && c[1] === "1.25"), e.calls);
e.player.playbackRate = 2;
check("TV v4: a change during playback is sent", e.calls[e.calls.length - 1][0] === "setSpeed" && e.calls[e.calls.length - 1][1] === "2", e.calls);
check("TV: no volume gesture", e.player.canSetVolume === false);
const sentSpeedCalls = e.calls.length;
e.player.playbackRate = 1;
check("TV v4: back to 1x is sent too", e.calls.length === sentSpeedCalls + 1 && e.calls[e.calls.length - 1][1] === "1");

for (const v of [3, 2, 1]) {
  e = envWith(v, true);
  e.player.playbackRate = 2;
  e.player.load({ url: "/a", kind: "file", startAt: 0, native: {} });
  e.window.NLNativeEvents.dispatch(JSON.stringify({ type: "started", positionSeconds: 0, durationSeconds: 100, playing: true }));
  check("TV v" + v + " app: speed is hidden and setSpeed is never called", e.player.canChangeSpeed === false && !e.calls.some((c) => c[0] === "setSpeed"), e.calls);
}

if (failed) { console.log(failed + " FAILED"); process.exit(1); }
console.log("all player extras checks passed");
