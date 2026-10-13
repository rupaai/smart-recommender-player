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

  var api = {
    SPEEDS: SPEEDS,
    DOUBLE_TAP_MS: DOUBLE_TAP_MS,
    tileFor: tileFor,
    chapterIndexAt: chapterIndexAt,
    chapterTicks: chapterTicks,
    segmentAt: segmentAt,
    speedLabel: speedLabel,
    nextSpeed: nextSpeed,
    halfOf: halfOf,
    tapAction: tapAction,
    swipeVolume: swipeVolume,
    isVerticalSwipe: isVerticalSwipe
  };
  global.NLPlayerExtras = api;
  if (typeof module !== "undefined" && module.exports) { module.exports = api; }
})(typeof window !== "undefined" ? window : globalThis);
