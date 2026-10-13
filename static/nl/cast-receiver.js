/* Northern Lights Chromecast receiver (M8a), runs on the Chromecast inside Google's Web Receiver framework (CAF v3).
 *
 * No cookies, no API calls of ours: the sender puts everything this page draws into the load request's
 * customData (title, episode, backdrop URL with its stream token). Progress comes from the player itself.
 *
 *   NLReceiver.start(window)       called on load; does nothing visible outside a Chromecast except in preview mode
 *   /nl/cast/receiver?preview=1&title=...&episode=...&backdrop=...&progress=0.4   draws the screen in any browser
 */
(function (global) {
  "use strict";

  var INTRO_MS = 6000;     // how long the title stays up after playback starts

  function $(id) { return global.document.getElementById(id); }

  function safeUrl(value) {
    // Only http(s) artwork; the sender is trusted but this page never builds markup from its strings.
    return typeof value === "string" && /^https?:\/\//i.test(value) ? value : "";
  }

  function fmt(sec) {
    sec = Math.max(0, Math.floor(Number(sec) || 0));
    var h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
    var ss = (s < 10 ? "0" : "") + s;
    return h > 0 ? h + ":" + (m < 10 ? "0" : "") + m + ":" + ss : m + ":" + ss;
  }

  var introTimer = null;

  function setMode(mode) {
    var body = global.document.body;
    ["idle", "loading", "playing", "paused"].forEach(function (m) { body.classList.toggle("nlr-" + m, m === mode); });
  }

  function intro() {
    var body = global.document.body;
    body.classList.add("nlr-intro");
    global.clearTimeout(introTimer);
    introTimer = global.setTimeout(function () { body.classList.remove("nlr-intro"); }, INTRO_MS);
  }

  function showTitle(data) {
    data = data || {};
    $("nlr-title").textContent = data.title || "";
    $("nlr-episode").textContent = data.episode || "";
    var url = safeUrl(data.backdrop);
    $("nlr-backdrop").style.backgroundImage = url ? "url(" + JSON.stringify(url) + ")" : "none";
  }

  function showProgress(current, total) {
    var pct = total > 0 ? Math.max(0, Math.min(100, current / total * 100)) : 0;
    $("nlr-fill").style.width = pct + "%";
    $("nlr-time").textContent = total > 0 ? fmt(current) + " / " + fmt(total) : "";
    var bar = global.document.querySelector(".nlr-progress");
    if (bar) { bar.setAttribute("aria-valuenow", String(Math.round(pct))); }
  }

  function preview() {
    var q = new URLSearchParams(global.location.search);
    showTitle({ title: q.get("title") || "Title", episode: q.get("episode") || "", backdrop: q.get("backdrop") || "" });
    var p = Number(q.get("progress")) || 0;
    showProgress(p * 6000, 6000);
    setMode(q.get("state") === "playing" ? "playing" : "paused");
    $("nlr-state").textContent = q.get("state") === "playing" ? "" : "Paused";
  }

  function start(win) {
    var q = new URLSearchParams(win.location.search);
    if (q.get("preview") === "1") { preview(); return; }
    var cf = win.cast && win.cast.framework;
    if (!cf) { setMode("idle"); return; }
    var context = cf.CastReceiverContext.getInstance();
    var manager = context.getPlayerManager();
    var E = cf.events.EventType;
    var P = cf.messages.PlayerState;
    setMode("idle");

    // The load request: pull the title out of customData before the player starts.
    manager.addEventListener(E.LOAD_START, function () { setMode("loading"); $("nlr-state").textContent = "Loading"; });
    manager.addEventListener(E.MEDIA_INFORMATION_CHANGED, function () {
      var info = manager.getMediaInformation && manager.getMediaInformation();
      var data = info && info.customData;
      var meta = info && info.metadata;
      showTitle({
        title: (data && data.title) || (meta && meta.title) || "",
        episode: (data && data.episode) || (meta && meta.subtitle) || "",
        backdrop: (data && data.backdrop) || ""
      });
    });
    manager.addEventListener(E.PLAYING, function () { setMode("playing"); $("nlr-state").textContent = ""; intro(); });
    manager.addEventListener(E.PAUSE, function () { setMode("paused"); $("nlr-state").textContent = "Paused"; });
    manager.addEventListener(E.BUFFERING, function (e) {
      if (e && e.isBuffering) { $("nlr-state").textContent = "Buffering"; } else if (manager.getPlayerState() === P.PLAYING) { $("nlr-state").textContent = ""; }
    });
    manager.addEventListener(E.SEEKED, function () { showProgress(manager.getCurrentTimeSec(), manager.getDurationSec()); intro(); });
    manager.addEventListener(E.TIME_UPDATE, function (e) {
      var current = e && typeof e.currentMediaTime === "number" ? e.currentMediaTime : manager.getCurrentTimeSec();
      showProgress(current, manager.getDurationSec());
    });
    manager.addEventListener(E.MEDIA_FINISHED, function () { setMode("idle"); showProgress(0, 0); });
    manager.addEventListener(E.ERROR, function () { $("nlr-state").textContent = "Can't play this"; setMode("paused"); });

    context.start({ disableIdleTimeout: false });
  }

  global.NLReceiver = { start: start, fmt: fmt, safeUrl: safeUrl };
  start(global);
})(window);
