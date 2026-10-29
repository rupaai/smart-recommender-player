/* Northern Lights player extras (M7a): the small, DOM-free logic behind seek-bar thumbnails, chapters,
 * skip intro / next episode, playback speed and the phone gestures. player.js does the drawing; this file
 * only answers questions, so it can be tested in plain node (tests/js/nl_player_extras_check.js). */
(function (global) {
  "use strict";

  var SPEEDS = [0.75, 1, 1.25, 1.5, 2];
  var DOUBLE_TAP_MS = 300;
  var SEGMENT_LEAD = 0.25;   // seconds: a segment counts a moment before its start, so the button is there on time
  var SEGMENT_TAIL = 0.75;   // and stops counting just before its end (nothing left to skip)

  function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }

  /* ---- thumbnails: which tile sheet, and where in it, shows second `seconds` ---- */
  function tileFor(trick, seconds) {
    if (!trick || !trick.url || !(trick.interval_seconds > 0)) { return null; }
    var per = trick.tile_width * trick.tile_height;
    if (!(per > 0)) { return null; }
    var n = clamp(Math.floor((Number(seconds) || 0) / trick.interval_seconds), 0, Math.max(0, (trick.count || 1) - 1));
    var sheet = Math.floor(n / per);
    var pos = n % per;
    var col = pos % trick.tile_width;
    var row = Math.floor(pos / trick.tile_width);
    return {
      url: trick.url.replace("{index}", String(sheet)),
      sheet: sheet,
      x: col * trick.width,
      y: row * trick.height,
      width: trick.width,
      height: trick.height,
      sheetWidth: trick.tile_width * trick.width,
      sheetHeight: trick.tile_height * trick.height
    };
  }

  /* ---- chapters ---- */
  function chapterIndexAt(chapters, seconds) {
    var idx = -1;
    for (var i = 0; i < (chapters || []).length; i++) {
      if (chapters[i].start_seconds <= seconds + 0.001) { idx = i; } else { break; }
    }
    return idx;
  }
  // Tick positions as fractions of the duration (0..1), skipping the one at the very start.
  function chapterTicks(chapters, duration) {
    if (!(duration > 0)) { return []; }
    return (chapters || []).filter(function (c) { return c.start_seconds > 1 && c.start_seconds < duration - 1; })
      .map(function (c) { return c.start_seconds / duration; });
  }

  /* ---- skip intro / next episode ---- */
  // The segment of this type the playhead is inside (with a little lead and tail), or null.
  function segmentAt(segments, type, seconds) {
    for (var i = 0; i < (segments || []).length; i++) {
      var s = segments[i];
      if (s.type === type && seconds >= s.start_seconds - SEGMENT_LEAD && seconds < s.end_seconds - SEGMENT_TAIL) { return s; }
    }
    return null;
  }

  /* ---- speed ---- */
  function speedLabel(rate) {
    return (rate === 1 ? "1" : String(rate)) + "×";
  }
  function nextSpeed(rate, step) {
    var i = SPEEDS.indexOf(rate);
    if (i < 0) { i = SPEEDS.indexOf(1); }
    return SPEEDS[clamp(i + step, 0, SPEEDS.length - 1)];
  }

  /* ---- "Ends at": the wall-clock time (ms) the film ends, at the current speed; null while the length is unknown ---- */
  function endsAt(nowMs, duration, current, rate) {
    var d = Number(duration), c = Number(current) || 0, r = Number(rate) || 1;
    if (!(d > 0) || !isFinite(d) || !(r > 0)) { return null; }
    return nowMs + Math.max(0, d - c) / r * 1000;
  }

  /* ---- Back on the TV remote, once menus and cards are closed: first hide the controls, then ask, then leave ---- */
  var BACK_AGAIN_MS = 3000;
  function backAction(controlsVisible, armedAt, nowMs) {
    if (controlsVisible) { return "hide"; }
    if (armedAt && nowMs - armedAt >= 0 && nowMs - armedAt <= BACK_AGAIN_MS) { return "leave"; }
    return "arm";
  }

  /* ---- TV info panel: the app's stats() numbers in plain words ---- */
  function num(v) { return typeof v === "number" && isFinite(v) ? v : null; }
  function infoLines(st) {
    st = st || {};
    var lines = [st.source === "converted" ? "Converted copy (not the original file)" : "Original file"];
    var w = num(st.width), h = num(st.height);
    var picture = [];
    if (w && h) { picture.push(w + "×" + h); }
    if (st.hdr) { picture.push(st.hdr); }
    if (picture.length) { lines.push("Picture: " + picture.join(", ")); }
    if (st.videoDecoder) { lines.push("Video decoder: " + st.videoDecoder); }
    var sound = "";
    if (st.audioPath === "passthrough") {
      sound = "sent to speakers untouched";
    } else if (st.audioPath === "decoded") {
      var ch = num(st.audioChannels);
      sound = "decoded on the TV, " + (ch === 2 ? "mixed for TV speakers" : (ch ? ch + " channels out" : "mixed for the TV"));
    }
    if (st.audioLabel) { lines.push("Audio track: " + st.audioLabel); }
    if (sound) { lines.push("Sound: " + sound); }
    var jump = num(st.lastSeekMs);
    lines.push("Last jump: " + (jump === null || jump < 0 ? "none yet" : (Math.round(jump / 100) / 10) + " s"));
    lines.push("Dropped frames: " + (num(st.droppedFrames) || 0));
    var ahead = num(st.bufferedAheadSeconds);
    lines.push("Loaded ahead: " + (ahead === null ? 0 : Math.round(ahead)) + " s");
    var mbps = num(st.bandwidthMbps);
    if (mbps) { lines.push("Connection: about " + Math.round(mbps) + " Mbps"); }
    var mem = num(st.memoryMb);
    if (mem !== null) { lines.push("App memory: " + Math.round(mem) + " MB"); }
    return lines;
  }

  /* ---- phone gestures ----
   * tapAction decides what a finished tap means given the previous tap:
   *   "double-left" / "double-right"  second tap within DOUBLE_TAP_MS in the same half: skip back / ahead
   *   "first"                          wait: it may become a double tap; if not, it is a single tap (toggle controls)
   * `prev` is {at, half} or null; `half` is "left" or "right". */
  function halfOf(x, width) { return x < width / 2 ? "left" : "right"; }
  function tapAction(prev, now, half) {
    if (prev && now - prev.at <= DOUBLE_TAP_MS && prev.half === half) { return "double-" + half; }
    return "first";
  }
  // A vertical swipe on the right half changes the volume by the fraction of the screen height it covers (one
  // full swipe over 60% of the height = 100%).
  function swipeVolume(startVolume, startY, y, height) {
    if (!(height > 0)) { return startVolume; }
    return Math.round(clamp(startVolume + (startY - y) / (height * 0.6), 0, 1) * 100) / 100;
  }
  // Is this a vertical swipe (rather than a tap or a sideways drag)?
  function isVerticalSwipe(dx, dy) {
    return Math.abs(dy) > 12 && Math.abs(dy) > Math.abs(dx) * 1.3;
  }

  /* Where the player's own pages live. The laptop app has its own root (the server renders it); the TV app and
   * the phone pages keep /nl/tv/ and /nl/. */
  function pageRoot(surface, rootAttr, isTv) {
    if (surface === "laptop") { return rootAttr || "/player/"; }
    return isTv ? "/nl/tv/" : "/nl/";
  }

  /* The address of the player for one episode (or movie): the laptop page, or /nl/play (with tv=1 on the TV). */
  function playUrl(surface, isTv, itemId, sourceId) {
    var qs = "?source=" + encodeURIComponent(sourceId);
    if (surface === "laptop") { return "/player/play/" + encodeURIComponent(itemId) + qs; }
    return "/nl/play/" + encodeURIComponent(itemId) + qs + (isTv ? "&tv=1" : "");
  }

  /* How the player takes input. bigScreen: the TV look (big sizes, dark style), true on the TV and the laptop app.
   * remote: the TV remote (D-pad focus, OK / Back keys, no pointer), true in the Android app and with ?tv=1, never on
   * the laptop. cast: the Chromecast sender (phones and laptops, not the remote). keys: which key handler runs. */
  function inputMode(surface, isNative, tvParam) {
    var laptop = surface === "laptop";
    var remote = !!(isNative || tvParam);
    return { laptop: laptop, bigScreen: remote || laptop, remote: remote, cast: !remote, keys: remote ? "remote" : "web" };
  }

  /* The device part of the /api/nl/play query: who is asking (client=tv for the Android app, client=laptop plus caps for
   * the laptop app, caps alone for phones and browsers, nothing while casting: the cast endpoint picks the profile) and
   * whether the viewer agreed to a converted copy (the TV and the laptop; never the phone page). Returns query parts. */
  function playDeviceParams(o) {
    var qs = [];
    if (!o.casting) {
      if (o.isNative) { qs.push("client=tv"); }
      else if (o.isLaptop) { qs.push("client=laptop", "caps=" + encodeURIComponent(o.caps)); }
      else { qs.push("caps=" + encodeURIComponent(o.caps)); }
    }
    if ((o.isNative || o.isLaptop) && o.allowConversion) { qs.push("allow_conversion=1"); }
    return qs;
  }

  // Quick skip presses group into one seek: each press moves a pending target, the seek fires once the presses stop.
  // o: {position(), duration(), seek(t), setTimeout, clearTimeout, ms}. press() returns {target, total}.
  function skipper(o) {
    var target = null, start = 0, timer = null;
    function flush() {
      if (timer !== null) { o.clearTimeout(timer); timer = null; }
      if (target === null) { return false; }
      var t = target;
      target = null;
      o.seek(t);
      return true;
    }
    function press(delta) {
      var d = o.duration();
      if (target === null) { start = o.position(); target = start; }
      target = Math.max(0, d ? Math.min(target + delta, d - 0.1) : target + delta);
      if (timer !== null) { o.clearTimeout(timer); }
      timer = o.setTimeout(flush, o.ms);
      return { target: target, total: Math.round(target - start) };
    }
    function cancel() {
      if (timer !== null) { o.clearTimeout(timer); timer = null; }
      target = null;
    }
    return { press: press, flush: flush, cancel: cancel, pending: function () { return target; } };
  }

  // The buffering spinner shows only if playback still waits after `ms`; a seek inside the buffer blips for a moment.
  // o: {show(), hide(), setTimeout, clearTimeout, ms}.
  function spinner(o) {
    var timer = null;
    function stop() { if (timer !== null) { o.clearTimeout(timer); timer = null; } }
    return {
      wait: function () { if (timer === null) { timer = o.setTimeout(function () { timer = null; o.show(); }, o.ms); } },
      clear: function () { stop(); o.hide(); }
    };
  }

  var api = {
    BACK_AGAIN_MS: BACK_AGAIN_MS,
    backAction: backAction,
    endsAt: endsAt,
    skipper: skipper,
    spinner: spinner,
    inputMode: inputMode,
    playDeviceParams: playDeviceParams,
    pageRoot: pageRoot,
    playUrl: playUrl,
    SPEEDS: SPEEDS,
    DOUBLE_TAP_MS: DOUBLE_TAP_MS,
    tileFor: tileFor,
    chapterIndexAt: chapterIndexAt,
    chapterTicks: chapterTicks,
    segmentAt: segmentAt,
    speedLabel: speedLabel,
    infoLines: infoLines,
    nextSpeed: nextSpeed,
    halfOf: halfOf,
    tapAction: tapAction,
    swipeVolume: swipeVolume,
    isVerticalSwipe: isVerticalSwipe
  };
  global.NLPlayerExtras = api;
  if (typeof module !== "undefined" && module.exports) { module.exports = api; }
})(typeof window !== "undefined" ? window : globalThis);
