// Behaviour check for static/nl/cast.js (M8a) against a FAKE chrome.cast / cast.framework (the real SDK does not run
// outside Chrome with a Chromecast on the network). tests/test_nl_cast.py calls it when node exists.
//   node tests/js/nl_cast_check.js
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

let failed = 0;
function check(label, ok, detail) {
  if (ok) { console.log("  ok  " + label); } else { failed += 1; console.log("  FAIL " + label + (detail !== undefined ? " -> " + JSON.stringify(detail) : "")); }
}

const src = fs.readFileSync(path.join(__dirname, "..", "..", "static", "nl", "cast.js"), "utf8");

// ---- fakes ----------------------------------------------------------------------------------------------

function fakeLocal() {
  const listeners = {};
  const log = [];
  return {
    log, listeners,
    emit(name, d) { (listeners[name] || []).forEach((f) => f(d)); },
    on(name, fn) { (listeners[name] = listeners[name] || []).push(fn); },
    load(s) { log.push(["load", s]); }, play() { log.push(["play"]); return Promise.resolve(); }, pause() { log.push(["pause"]); },
    seek(s) { log.push(["seek", s]); },
    currentTime: 11, duration: 100, paused: false, buffered: 3, volume: 0.8, muted: false, canSetVolume: true,
    canChangeSpeed: true, playbackRate: 1, canStyleSubtitles: true, canDelaySubtitles: true, subtitleDelay: 0, isFullscreen: false,
    setTextTracks() {}, showTextTrack() {}, setSubtitleDelay() {}, setSubtitleStyle() {}, toggleFullscreen() {}, destroy() {}
  };
}

function makeWindow(opts) {
  opts = opts || {};
  const scripts = [];
  const win = {
    isSecureContext: opts.secure !== false,
    scripts,
    document: {
      head: { appendChild(s) { scripts.push(s); } },
      createElement() { return {}; }
    },
    setTimeout: (fn, ms) => setTimeout(fn, ms),
    Promise
  };
  if (opts.chrome !== false) { win.chrome = {}; }
  if (opts.native) { win.NorthernLightsNative = {}; }
  win.window = win;
  vm.createContext(win);
  vm.runInContext(src, win);
  return win;
}

// A fake Cast SDK. Everything the sender touches, nothing more.
function installSdk(win, opts) {
  opts = opts || {};
  const ctxListeners = {};
  const rcListeners = {};
  const sdk = { loadRequests: [], editRequests: [], controllerCalls: [], ended: [], contextOptions: null };
  const mediaSession = { idleReason: null, editTracksInfo(req, ok) { sdk.editRequests.push(req.activeTrackIds); ok(); } };
  const session = {
    getCastDevice: () => ({ friendlyName: "Living Room TV" }),
    getMediaSession: () => mediaSession,
    loadMedia(req) { sdk.loadRequests.push(req); return Promise.resolve(opts.loadError); }
  };
  const player = { isPaused: true, currentTime: 0, duration: 0, volumeLevel: 1, isMuted: false, mediaInfo: opts.mediaInfo || null };
  sdk.session = session; sdk.mediaSession = mediaSession; sdk.player = player;
  sdk.castState = opts.castState || "NOT_CONNECTED";
  sdk.hasSession = !!opts.resumed;
  const context = {
    setOptions(o) { sdk.contextOptions = o; },
    getCastState: () => sdk.castState,
    getCurrentSession: () => (sdk.hasSession ? session : null),
    addEventListener(type, fn) { (ctxListeners[type] = ctxListeners[type] || []).push(fn); },
    requestSession() {
      if (opts.pickerCancelled) { return Promise.reject("cancel"); }
      sdk.hasSession = true; sdk.castState = "CONNECTED";
      sdk.fireCastState("CONNECTED");
      sdk.fireSession("SESSION_STARTED");
      return Promise.resolve();
    },
    endCurrentSession(stop) {
      sdk.ended.push(stop); sdk.hasSession = false; sdk.castState = "NOT_CONNECTED";
      sdk.fireSession("SESSION_ENDED"); sdk.fireCastState("NOT_CONNECTED");
    }
  };
  sdk.fireCastState = (s) => (ctxListeners.caststatechanged || []).forEach((f) => f({ castState: s }));
  sdk.fireSession = (s) => (ctxListeners.sessionstatechanged || []).forEach((f) => f({ sessionState: s }));
  sdk.change = (field, value) => { player[field] = value; (rcListeners.anyChanged || []).forEach((f) => f({ field, value })); };
  win.chrome = win.chrome || {};
  win.chrome.cast = {
    AutoJoinPolicy: { ORIGIN_SCOPED: "origin_scoped" },
    Image: function (url) { this.url = url; },
    media: {
      DEFAULT_MEDIA_RECEIVER_APP_ID: "CC1AD845",
      StreamType: { BUFFERED: "BUFFERED" }, TrackType: { TEXT: "TEXT" }, TextTrackType: { SUBTITLES: "SUBTITLES" },
      MediaInfo: function (id, type) { this.contentId = id; this.contentType = type; },
      GenericMediaMetadata: function () {},
      Track: function (id, type) { this.trackId = id; this.type = type; },
      LoadRequest: function (info) { this.media = info; },
      EditTracksInfoRequest: function (ids) { this.activeTrackIds = ids; }
    }
  };
  win.cast = {
    framework: {
      CastContext: { getInstance: () => context },
      CastContextEventType: { CAST_STATE_CHANGED: "caststatechanged", SESSION_STATE_CHANGED: "sessionstatechanged" },
      CastState: { NO_DEVICES_AVAILABLE: "NO_DEVICES_AVAILABLE", NOT_CONNECTED: "NOT_CONNECTED", CONNECTING: "CONNECTING", CONNECTED: "CONNECTED" },
      SessionState: { SESSION_STARTED: "SESSION_STARTED", SESSION_RESUMED: "SESSION_RESUMED", SESSION_ENDED: "SESSION_ENDED" },
      RemotePlayer: function () { return player; },
      RemotePlayerController: function () {
        return {
          addEventListener(type, fn) { (rcListeners[type] = rcListeners[type] || []).push(fn); },
          playOrPause() { sdk.controllerCalls.push("playOrPause"); player.isPaused = !player.isPaused; },
          seek() { sdk.controllerCalls.push("seek:" + player.currentTime); },
          setVolumeLevel() { sdk.controllerCalls.push("volume:" + player.volumeLevel); },
          muteOrUnmute() { sdk.controllerCalls.push("mute"); player.isMuted = !player.isMuted; }
        };
      },
      RemotePlayerEventType: { ANY_CHANGE: "anyChanged" }
    }
  };
  return sdk;
}

const STREAM = {
  url: "http://192.168.1.50:8000/nl/jf/videos/jfA/master.m3u8?MediaSourceId=ms&t=v1.9.jfa.sig",
  kind: "hls", title: "Alpha", episode: { season: 1, episode: 3, name: "Pilot" }, duration_seconds: 3000,
  media_item_id: 7, source_id: 21,
  cast: { content_type: "application/x-mpegURL", backdrop: "http://192.168.1.50:8000/nl/jf/Items/jfA/Images/Backdrop?t=v1.9.jfa.sig", poster: "http://p/x?t=1" },
  subtitle_tracks: [
    { index: 4, kind: "text", label: "English", language: "eng", url: "http://192.168.1.50:8000/nl/jf/Videos/jfA/ms/Subtitles/4/0/Stream.vtt?t=v1.9.jfa.sig" },
    { index: 7, kind: "image", label: "French · PGS", language: "fre", url: null }
  ]
};

const tick = () => new Promise((r) => setTimeout(r, 5));

async function main() {
  // ---- feature detection
  console.log("feature detection");
  let win = makeWindow({ secure: false });
  check("not eligible without a secure context", win.NLCast.eligible(win) === false);
  win = makeWindow({ chrome: false });
  check("not eligible outside Chromium", win.NLCast.eligible(win) === false);
  win = makeWindow({ native: true });
  check("not eligible inside the TV app", win.NLCast.eligible(win) === false);
  win = makeWindow();
  check("eligible in Chrome over HTTPS", win.NLCast.eligible(win) === true);

  let local = fakeLocal();
  win = makeWindow({ secure: false });
  let kit = win.NLCast.attach(win, local, true);
  kit.cast.init({ enabled: true, app_id: "CC1AD845" });
  check("insecure page: the SDK script is never added, the button never shows", win.scripts.length === 0 && kit.cast.state === "unavailable");
  win = makeWindow();
  kit = win.NLCast.attach(win, fakeLocal(), true);
  kit.cast.init({ enabled: false, app_id: "CC1AD845" });
  check("casting not set up on the server: no SDK script", win.scripts.length === 0);
  kit = win.NLCast.attach(win, fakeLocal(), false);
  kit.cast.init({ enabled: true, app_id: "CC1AD845" });
  check("the TV page (enabled=false): no SDK script", win.scripts.length === 0);

  kit = win.NLCast.attach(win, fakeLocal(), true);
  kit.cast.init({ enabled: true, app_id: "CC1AD845" });
  check("the official SDK is loaded from gstatic with loadCastFramework=1",
    win.scripts.length === 1 && win.scripts[0].src === "https://www.gstatic.com/cv/js/sender/v1/cast_sender.js?loadCastFramework=1", win.scripts.map((s) => s.src));
  check("and flagged async", win.scripts[0].async === true);
  win.scripts[0].onerror();
  check("a blocked SDK leaves casting unavailable", kit.cast.state === "unavailable");
  win.__onGCastApiAvailable(false);
  check("isAvailable=false leaves it unavailable", kit.cast.state === "unavailable");

  // ---- states
  console.log("Cast button states");
  win = makeWindow();
  const sdk = installSdk(win);
  local = fakeLocal();
  kit = win.NLCast.attach(win, local, true);
  const states = [];
  const connected = [];
  const ended = [];
  kit.cast.on("state", (s) => states.push(s));
  kit.cast.on("connected", (e) => connected.push(e));
  kit.cast.on("ended", (e) => ended.push(e));
  kit.cast.init({ enabled: true, app_id: "ABCD1234" });
  check("SDK already present: no script is added", win.scripts.length === 0);
  check("receiver app id from the config and ORIGIN_SCOPED auto-join",
    sdk.contextOptions.receiverApplicationId === "ABCD1234" && sdk.contextOptions.autoJoinPolicy === "origin_scoped", sdk.contextOptions);
  check("devices around: the button may show", kit.cast.state === "ready" && states[0] === "ready", states);
  sdk.castState = "NO_DEVICES_AVAILABLE"; sdk.fireCastState("NO_DEVICES_AVAILABLE");
  check("no devices: state no-devices", kit.cast.state === "no-devices");
  sdk.castState = "CONNECTING"; sdk.fireCastState("CONNECTING");
  check("connecting", kit.cast.state === "connecting");
  sdk.castState = "NOT_CONNECTED"; sdk.fireCastState("NOT_CONNECTED");

  // default receiver when no app id
  const w2 = makeWindow(); const s2 = installSdk(w2);
  w2.NLCast.attach(w2, fakeLocal(), true).cast.init({ enabled: true });
  check("no app id: the Default Media Receiver", s2.contextOptions.receiverApplicationId === "CC1AD845", s2.contextOptions);

  // ---- the switch before connecting
  console.log("the adapter switch");
  const p = kit.player;
  check("before casting the page talks to the local player", p.currentTime === 11 && p.duration === 100 && p.paused === false && p.canChangeSpeed === true && kit.active() === false);
  const seen = [];
  p.on("pause", () => seen.push("pause"));
  p.on("timeupdate", () => seen.push("tu"));
  local.emit("pause");
  check("local events reach the page while local is in charge", seen.join() === "pause", seen);
  p.seek(30);
  check("calls go to the local player", local.log.some((l) => l[0] === "seek" && l[1] === 30));

  // ---- connect
  console.log("connecting");
  await kit.cast.request();
  check("requesting opens a session and says connected, with the device name",
    connected.length === 1 && connected[0].deviceName === "Living Room TV" && connected[0].resumed === false, connected);
  check("state is connected", kit.cast.state === "connected");
  kit.activate();
  check("activate hands control to the TV", kit.active() === true);
  check("TV traits: no speed, no subtitle style or delay, volume works",
    p.canChangeSpeed === false && p.canStyleSubtitles === false && p.canDelaySubtitles === false && p.canSetVolume === true);
  seen.length = 0;
  local.emit("pause");
  check("local events no longer reach the page", seen.length === 0, seen);

  // ---- load
  console.log("loading a stream on the TV");
  p.load({ url: STREAM.url, kind: "hls", startAt: 2530, cast: STREAM });
  p.showTextTrack(4);   // the page calls this right after load
  await tick();
  const req = sdk.loadRequests[0];
  check("one load request", sdk.loadRequests.length === 1);
  check("media URL and content type come from the server's cast block", req.media.contentId === STREAM.url && req.media.contentType === "application/x-mpegURL", req.media);
  check("start position, autoplay, buffered stream", req.currentTime === 2530 && req.autoplay === true && req.media.streamType === "BUFFERED" && req.media.duration === 3000);
  check("title and episode as metadata", req.media.metadata.title === "Alpha" && req.media.metadata.subtitle === "S1 · E3 · Pilot", req.media.metadata);
  check("backdrop image (tokenised LAN URL)", req.media.metadata.images[0].url === STREAM.cast.backdrop);
  check("custom data for the receiver (no cookies needed)", req.media.customData.nl === 1 && req.media.customData.item_id === 7
    && req.media.customData.source_id === 21 && req.media.customData.title === "Alpha" && req.media.customData.backdrop === STREAM.cast.backdrop, req.media.customData);
  check("only the text subtitle is a Cast track, as WebVTT, id 1000+index",
    req.media.tracks.length === 1 && req.media.tracks[0].trackId === 1004 && req.media.tracks[0].trackContentType === "text/vtt"
    && req.media.tracks[0].trackContentId === STREAM.subtitle_tracks[0].url && req.media.tracks[0].name === "English", req.media.tracks);
  check("the subtitle chosen right after load is switched on once loaded", sdk.editRequests.length === 1 && sdk.editRequests[0][0] === 1004, sdk.editRequests);
  check("no Jellyfin key anywhere in the request", JSON.stringify(req).indexOf("api_key") < 0);

  // ---- events from the TV
  console.log("the TV as a player");
  const ev = [];
  ["play", "pause", "playing", "waiting", "timeupdate", "seeked", "ended", "volumechange", "loadedmetadata", "error"].forEach((n) => p.on(n, (d) => ev.push(n + (d && d.message ? ":" + d.message : ""))));
  sdk.change("duration", 3000);
  check("duration arrives", p.duration === 3000 && ev.includes("loadedmetadata"), ev);
  sdk.change("isPaused", false); sdk.change("playerState", "PLAYING");
  check("playing", p.paused === false && ev.includes("play") && ev.includes("playing"), ev);
  sdk.change("currentTime", 2540);
  check("position follows the TV", p.currentTime === 2540 && ev.includes("timeupdate"));
  sdk.change("playerState", "BUFFERING");
  check("buffering", ev.includes("waiting"));
  sdk.change("playerState", "PLAYING");

  console.log("phone as remote");
  sdk.controllerCalls.length = 0;
  p.pause();
  check("pause -> the TV pauses", sdk.controllerCalls.join() === "playOrPause" && sdk.player.isPaused === true, sdk.controllerCalls);
  sdk.change("isPaused", true);
  check("paused state comes back", p.paused === true && ev.includes("pause"));
  sdk.controllerCalls.length = 0;
  p.pause();
  check("pausing twice does not toggle it back", sdk.controllerCalls.length === 0);
  await p.play();
  check("play -> the TV resumes", sdk.controllerCalls.join() === "playOrPause" && sdk.player.isPaused === false, sdk.controllerCalls);
  sdk.controllerCalls.length = 0;
  p.seek(2600);
  check("seek (also what skip uses) -> the TV seeks, position is immediate", sdk.controllerCalls.join() === "seek:2600" && p.currentTime === 2600, sdk.controllerCalls);
  ev.length = 0;
  sdk.change("playerState", "PLAYING");
  check("seeked is announced when playback settles", ev.includes("seeked"), ev);
  sdk.controllerCalls.length = 0;
  p.volume = 0.4;
  check("volume -> the TV", sdk.controllerCalls.join() === "volume:0.4" && p.volume === 0.4);
  p.muted = true;
  check("mute -> the TV", sdk.controllerCalls.includes("mute") && p.muted === true);
  sdk.change("volumeLevel", 0.3);
  check("volume changes on the TV come back", p.volume === 0.3);

  console.log("subtitle tracks");
  p.showTextTrack(null);
  check("subtitles off -> empty active track list", sdk.editRequests[sdk.editRequests.length - 1].length === 0, sdk.editRequests);
  const n = sdk.editRequests.length;
  p.showTextTrack(null);
  check("no repeated request when nothing changed", sdk.editRequests.length === n);
  p.showTextTrack(4);
  check("subtitles on -> the track id", sdk.editRequests[sdk.editRequests.length - 1][0] === 1004);

  console.log("audio change = new load at the same place");
  p.load({ url: STREAM.url, kind: "hls", startAt: 2600, cast: STREAM });
  await tick();
  check("a second load request at the new position", sdk.loadRequests.length === 2 && sdk.loadRequests[1].currentTime === 2600);
  check("the active subtitle track is carried over", sdk.loadRequests[1].activeTrackIds[0] === 1004, sdk.loadRequests[1].activeTrackIds);

  console.log("the end of the title");
  ev.length = 0;
  sdk.mediaSession.idleReason = "FINISHED";
  sdk.change("playerState", "PLAYING");
  sdk.change("playerState", "IDLE");
  check("finished is announced once", ev.filter((x) => x === "ended").length === 1, ev);
  sdk.change("playerState", "IDLE");
  check("and not again", ev.filter((x) => x === "ended").length === 1);
  sdk.mediaSession.idleReason = null;

  console.log("load errors");
  const w3 = makeWindow(); const s3 = installSdk(w3, { loadError: "LOAD_FAILED" });
  const k3 = w3.NLCast.attach(w3, fakeLocal(), true);
  k3.cast.init({ enabled: true, app_id: "X1" });
  await k3.cast.request(); k3.activate();
  const errs = [];
  k3.player.on("error", (e) => errs.push(e));
  k3.player.load({ url: "u", kind: "file", startAt: 0, cast: Object.assign({}, STREAM, { cast: { content_type: "video/mp4" } }) });
  await tick();
  check("the TV's refusal becomes a player error", errs.length === 1 && /LOAD_FAILED/.test(errs[0].message), errs);
  const k3b = w3.NLCast.attach(w3, fakeLocal(), true);
  const errs2 = [];
  k3b.activate(); k3b.player.on("error", (e) => errs2.push(e));
  k3b.player.load({ url: "u", kind: "file", startAt: 0 });
  check("a load without a cast stream is an error, not a crash", errs2.length === 1, errs2);
  check("nothing was sent for it", s3.loadRequests.length === 1);

  // ---- hand-back
  console.log("disconnecting");
  kit.player.seek(2700);
  kit.cast.end(true);
  check("ending the session reports the TV's last position", ended.length === 1 && ended[0].position === 2700, ended);
  check("and asks the receiver to stop", sdk.ended[0] === true);
  kit.deactivate();
  check("control returns to the local player", kit.active() === false && kit.player.currentTime === 11);
  seen.length = 0;
  local.emit("pause");
  check("local events reach the page again", seen.includes("pause"), seen);

  // ---- resumed session
  console.log("a session that was already running when the page opened");
  const w4 = makeWindow();
  const s4 = installSdk(w4, { resumed: true, castState: "CONNECTED", mediaInfo: { customData: { nl: 1, item_id: 7, source_id: 21 } } });
  s4.player.currentTime = 1234;
  const k4 = w4.NLCast.attach(w4, fakeLocal(), true);
  const c4 = [];
  k4.cast.on("connected", (e) => c4.push(e));
  k4.cast.init({ enabled: true, app_id: "X1" });
  check("it is announced as resumed, with the title and position the TV is at",
    c4.length === 1 && c4[0].resumed === true && c4[0].item.item_id === 7 && c4[0].position === 1234, c4);
  const w5 = makeWindow();
  const s5 = installSdk(w5, { resumed: true, castState: "CONNECTED", mediaInfo: { customData: { other: 1 } } });
  void s5;
  const k5 = w5.NLCast.attach(w5, fakeLocal(), true);
  const c5 = [];
  k5.cast.on("connected", (e) => c5.push(e));
  k5.cast.init({ enabled: true, app_id: "X1" });
  check("media that is not ours has no item", c5.length === 1 && c5[0].item === null, c5);

  console.log("picker cancelled");
  const w6 = makeWindow(); installSdk(w6, { pickerCancelled: true });
  const k6 = w6.NLCast.attach(w6, fakeLocal(), true);
  k6.cast.init({ enabled: true, app_id: "X1" });
  let threw = false;
  await k6.cast.request().catch(() => { threw = true; });
  check("closing the device picker is not an error", threw === false);

  if (failed) { console.log("\n" + failed + " FAILED"); process.exit(1); }
  console.log("\nall cast.js checks passed");
}

main().catch((e) => { console.log("  FAIL crashed -> " + (e && e.stack || e)); process.exit(1); });
