/* Northern Lights player UI (M1).
 * Everything that touches the video goes through the adapter in player-core.js. */
(function () {
  "use strict";

  var SKIP_CHOICES = [5, 10, 15, 30, 60];
  var HIDE_AFTER_MS = 3000;
  var TV_HIDE_AFTER_MS = 5000;
  var PROGRESS_EVERY_MS = 10000;
  var LONG_PRESS_MS = 500;
  var SKIP_GROUP_MS = 350;      // skip presses closer together than this make one seek
  var SPINNER_AFTER_MS = 700;   // the buffering spinner waits this long before it shows
  var UNREACHABLE = "Can’t reach the movie server. Is mars on?";
  var CONVERT_FAILED = "The server couldn’t convert this video.";
  var CONVERT_DETAIL = "Jellyfin failed to transcode it. Check Jellyfin’s FFmpeg log.";
  var UNPLAYABLE = "This video’s format can’t play in this browser.";
  // M10c, P20: say which format it is when the stream says (HEVC video, DTS sound), and what to try.
  function unplayableText() {
    var f = (S.stream && S.stream.conversion && S.stream.conversion.formats) || {};
    var named = [];
    if (f.video) { named.push(f.video + " video"); }
    if (f.audio) { named.push(f.audio + " sound"); }
    var device = isNative ? "This TV" : "This browser";
    if (!named.length) { return UNPLAYABLE; }
    return device + " can’t play this format (" + named.join(", ") + "). Try Picture, then Quality, and choose a lower setting: the server then converts it.";
  }
  var NEXT_CARD_SECONDS = 30;   // the next-episode card appears this long before the end (P11)
  var COUNTDOWN_SECONDS = 10;   // and counts down for this long before starting the episode
  var SUB_WARM_AFTER_MS = 8000; // TV: fetch the text subtitles this long after a stream starts (see warmSubtitles)
  var SUB_WARM_MAX = 8;
  var DELAY_STEP = 0.25;        // subtitle delay buttons (P16)
  var DELAY_MAX = 60;
  var SUB_SIZES = ["S", "M", "L", "XL"];
  var SUB_BACKGROUNDS = [["none", "None"], ["shadow", "Shadow"], ["box", "Box"]];
  var SUB_POSITIONS = ["Normal", "Raised", "High"];
  var SUB_POLL_MS = 1200;       // after a download, how often we ask whether Jellyfin lists the new track
  var SUB_POLL_TRIES = 5;

  function $(id) { return document.getElementById(id); }
  var root = $("nl-player");
  var video = $("nl-video");
  var itemId = document.body.getAttribute("data-item-id");
  var query = new URLSearchParams(window.location.search);
  var urlSource = Number(query.get("source")) || null;   // which episode of a show (media_sources.id)
  var urlResume = query.get("resume");                    // "1" resume, "0" start over: skip the prompt
  var localPlayer = NLPlayerCore.create(video);
  var watchSession = NLWatchSession.create(function (url, options) { return fetch(url, options); });
  // On a TV (?tv=1, or inside the Android app) everything works with a remote; phones and laptops are untouched.
  var isNative = NLPlayerCore.isNative();          // the Android app decodes through Media3 behind this transparent page
  // Bridge v2 (pause/resume/seekTo/setSubtitle + "time" events): the web controls drive the native player.
  // An old app (v1) draws its own controls: the page then hides its own and is only the shell.
  var isNativeV2 = isNative && Number(NLPlayerCore.nativeVersion()) >= 2;
  var isNativeV1 = isNative && !isNativeV2;
  var surface = document.body.getAttribute("data-surface") || "web";   // web, tv or laptop (the server decides)
  var isLaptop = surface === "laptop";
  // Two meanings (NLPlayerExtras.inputMode): the big-screen look (TV and laptop: sizes, dark style) and the remote
  // control input (Android app, ?tv=1: D-pad focus, OK / Back keys, no pointer, no cast). The laptop is big-screen
  // with a mouse and the normal web keys.
  var mode = NLPlayerExtras.inputMode(surface, isNative, query.get("tv") === "1");
  var bigScreen = mode.bigScreen;
  var remote = mode.remote;
  if (isLaptop) {
    document.documentElement.classList.add("laptop");
    root.classList.add("laptop");
  }
  if (bigScreen) {
    document.documentElement.classList.add("tv");
    root.classList.add("tv");
  }
  if (isNativeV2) {
    document.documentElement.classList.add("native");   // transparent page: the video is behind the WebView
    root.classList.add("native");
  }
  if (isNativeV1) { root.classList.add("native-v1"); }
  // Where this app's title and show pages live: /nl/, /nl/tv/ (also in the Android app) or /player/.
  var PAGE_ROOT = NLPlayerExtras.pageRoot(surface, document.body.getAttribute("data-root"), bigScreen);
  // M8 casting: `player` is the <video> adapter, or the Chromecast while one is connected (cast.js swaps it).
  // Phones and laptops only (the button shows when a Chromecast is found); the TV app never casts.
  var castKit = window.NLCast ? NLCast.attach(window, localPlayer, mode.cast) : null;
  var player = castKit ? castKit.player : localPlayer;
  function casting() { return !!(castKit && castKit.active()); }

  var S = {
    stream: null,
    skip: 10,
    audioIndex: null,
    burnIndex: -1,        // picture subtitle currently burned in by Jellyfin, -1 = none
    textIndex: null,      // text subtitle currently shown, null = none
    started: false,       // "start" sent for the current stream
    stopped: true,        // "stop" sent (or nothing started yet)
    scrubbing: false,
    wasVisible: true,
    errors: 0,
    switching: false,
    allowConversion: false,   // TV and laptop: the viewer chose "Play a converted copy" because the original cannot be played
    retry: null,
    progressTimer: null,
    hideTimer: null,
    toastTimer: null,
    menuOpen: false,
    sourceId: urlSource,  // episode being played (null for a movie, or a show's first file)
    next: null,           // {source_id, code, name, autoplay} while a next episode exists
    nextCancelled: false, // Cancel pressed for this episode
    advancing: false,     // already switching to the next episode
    backHref: PAGE_ROOT,
    nextPromise: null,    // the next-episode lookup in flight (the native player wants the label up front)
    subStyle: { size: "M", background: "box", raise: 0 },   // P15, saved in the person's prefs (NLPrefs; localStorage is the fallback)
    subDelay: 0,          // P16, seconds; per file, so never saved
    // M7a polish
    extras: null,         // {trickplay, chapters, segments, skip_intro, is_show} from /api/nl/player-extras
    extrasKey: 0,         // bumps with every episode so a late answer for the previous one is ignored
    skipIntro: false,     // this viewer's "always skip intro" for the show
    autoSkipped: {},      // intro segments already skipped by the switch (once each)
    segDismissed: {},     // segments whose button was dismissed with Back (TV)
    segFocused: {},       // segments whose button already took the remote's focus
    segCur: null,         // the segment the button is showing for
    speed: 1,             // P17
    backArmedAt: 0,       // when Back last asked "press Back again" (TV)
    quality: null,        // M10c, P19: this viewing's quality: null = the person's default, 0 = Auto, else kbit/s
    fit: "fit",           // M10c, P18: fit | fill | original (this viewing; the default is a setting)
    convertNoted: "",     // the conversion note already shown for this film
    thumbTimer: null,
    pointerType: "mouse",
    quietUntil: 0,
    closedMenuByTap: false
  };
  var X = window.NLPlayerExtras;

  /* ---------------------------------------------------------- small helpers */

  function lsGet(key) { try { return window.localStorage.getItem(key); } catch (e) { return null; } }
  function lsSet(key, value) { try { window.localStorage.setItem(key, String(value)); } catch (e) { /* private mode */ } }

  function fmt(sec) {
    if (!isFinite(sec) || sec < 0) { sec = 0; }
    sec = Math.floor(sec);
    var h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
    var ss = (s < 10 ? "0" : "") + s;
    return h > 0 ? h + ":" + (m < 10 ? "0" : "") + m + ":" + ss : m + ":" + ss;
  }

  function duration() {
    var d = player.duration;
    if (isFinite(d) && d > 0) { return d; }
    return (S.stream && S.stream.duration_seconds) || 0;
  }

  function svg(inner) { return inner; }
  var ICONS = {
    play: svg('<path class="fill" d="M8 5v14l11-7z"/>'),
    pause: svg('<path class="fill" d="M6 5h4v14H6zM14 5h4v14h-4z"/>'),
    back: function (n) {
      return '<path d="M4 12a8 8 0 1 0 3-6.2"/><path d="M4 3v5h5"/><text x="12" y="15">' + n + "</text>";
    },
    fwd: function (n) {
      return '<path d="M20 12a8 8 0 1 1-3-6.2"/><path d="M20 3v5h-5"/><text x="12" y="15">' + n + "</text>";
    },
    vol: svg('<path d="M4 9v6h4l5 4V5L8 9z"/><path d="M16 9a4 4 0 0 1 0 6M18.5 6.5a8 8 0 0 1 0 11"/>'),
    muted: svg('<path d="M4 9v6h4l5 4V5L8 9z"/><path d="M17 9l5 6M22 9l-5 6"/>')
  };

  function toast(text, ms) {
    var el = $("nl-toast");
    el.textContent = text;
    el.classList.add("on");
    clearTimeout(S.toastTimer);
    S.toastTimer = setTimeout(function () { el.classList.remove("on"); }, ms || 1100);
  }

  /* -------------------------------------------------------- control hiding */

  function screenOpen() {
    return !$("nl-resume").hidden || !$("nl-react").hidden || !$("nl-error").hidden || !$("nl-help").hidden || !$("nl-subfind").hidden;
  }
  function canHide() {
    return !player.paused && !S.menuOpen && !S.scrubbing && !screenOpen() && !casting();
  }
  function showControls() {
    root.classList.add("controls-visible");
    clearTimeout(S.hideTimer);
    if (canHide()) {
      S.hideTimer = setTimeout(function () {
        if (canHide()) {
          root.classList.remove("controls-visible");
          // Back to "arrows skip, OK pauses" once the controls are gone.
          if (remote && $("nl-controls").contains(document.activeElement)) { document.activeElement.blur(); }
        }
      }, remote ? TV_HIDE_AFTER_MS : HIDE_AFTER_MS);
    }
  }
  function controlsVisible() { return root.classList.contains("controls-visible"); }
  function hideControlsNow() {
    clearTimeout(S.hideTimer);
    root.classList.remove("controls-visible");
    if ($("nl-controls").contains(document.activeElement)) { document.activeElement.blur(); }
  }

  /* ------------------------------------------------------------------ menus */

  var menu = $("nl-menu");
  var menuOpener = null;
  function closeMenu() {
    stopInfoPanel();
    menu.hidden = true;
    menu.innerHTML = "";
    S.menuOpen = false;
    releaseSubtitleHold();
    showControls();
    if (remote && menuOpener && document.activeElement !== menuOpener) { menuOpener.focus(); }   // the remote keeps its place
    menuOpener = null;
  }
  function openMenu(title, items) {
    stopInfoPanel();
    menuOpener = document.activeElement && document.activeElement !== document.body ? document.activeElement : null;
    menu.innerHTML = "";
    var h = document.createElement("div");
    h.className = "nl-menu-title";
    h.textContent = title;
    menu.appendChild(h);
    items.forEach(function (it) {
      if (it.kind === "stepper") { menu.appendChild(stepperRow(it)); return; }
      if (it.kind === "note") {
        var n = document.createElement("div");
        n.className = "nl-menu-note";
        n.textContent = it.text;
        menu.appendChild(n);
        return;
      }
      var b = document.createElement("button");
      b.type = "button";
      var tick = document.createElement("span");
      tick.className = "tick";
      var label = document.createElement("span");
      if (it.kind === "action" || it.kind === "cycle") {
        // An action closes the menu; a cycle changes its value and stays open (the label is updated in place).
        b.setAttribute("role", "menuitem");
        label.textContent = it.kind === "cycle" ? it.getLabel() : it.label;
        if (it.kind === "action") { b.className = it.top ? "nl-menu-action nl-menu-action-top" : "nl-menu-action"; }
        b.addEventListener("click", function () {
          if (it.kind === "action") { closeMenu(); it.onSelect(); return; }
          it.onSelect();
          label.textContent = it.getLabel();
        });
      } else {
        b.setAttribute("role", "menuitemradio");
        b.setAttribute("aria-checked", it.selected ? "true" : "false");
        tick.textContent = it.selected ? "✓" : "";
        label.textContent = it.label;
        b.addEventListener("click", function () { closeMenu(); it.onSelect(); });
      }
      b.appendChild(tick);
      b.appendChild(label);
      menu.appendChild(b);
    });
    menu.hidden = false;
    S.menuOpen = true;
    showControls();
    var first = menu.querySelector("button[aria-checked='true']") || menu.querySelector("button");
    if (first) { first.focus(); }
  }
  // A row with a value and a - / + pair, e.g. the subtitle delay. The menu stays open.
  function stepperRow(it) {
    var row = document.createElement("div");
    row.className = "nl-stepper";
    row.setAttribute("role", "group");
    var label = document.createElement("span");
    label.className = "nl-stepper-label";
    label.textContent = it.getLabel();
    row.setAttribute("aria-label", it.name);
    function step(sign) {
      return function () { it.onStep(sign); label.textContent = it.getLabel(); };
    }
    var minus = document.createElement("button");
    minus.type = "button";
    minus.textContent = "−";
    minus.setAttribute("aria-label", it.name + " minus " + it.stepText);
    minus.addEventListener("click", step(-1));
    var plus = document.createElement("button");
    plus.type = "button";
    plus.textContent = "+";
    plus.setAttribute("aria-label", it.name + " plus " + it.stepText);
    plus.addEventListener("click", step(1));
    row.appendChild(label);
    row.appendChild(minus);
    row.appendChild(plus);
    return row;
  }
  document.addEventListener("pointerdown", function (e) {
    if (S.menuOpen && !menu.contains(e.target) && !e.target.closest(".nl-text-btn, .nl-chip")) {
      S.closedMenuByTap = true;   // that tap only closes the menu (see the gestures below)
      closeMenu();
    }
  }, true);

  /* ------------------------------------------------------------- skip length */

  function renderSkip() {
    $("nl-skip-chip").textContent = S.skip + " s";
    $("nl-skip-back").innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true">' + ICONS.back(S.skip) + "</svg>";
    $("nl-skip-fwd").innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true">' + ICONS.fwd(S.skip) + "</svg>";
    $("nl-skip-back").setAttribute("aria-label", "Skip back " + S.skip + " seconds");
    $("nl-skip-fwd").setAttribute("aria-label", "Skip ahead " + S.skip + " seconds");
  }
  function setSkip(value, announce) {
    S.skip = value;
    lsSet("nl.skipSeconds", value);
    renderSkip();
    if (announce !== false) { toast("Skip " + value + " s"); }
  }
  function cycleSkip(step) {
    var i = SKIP_CHOICES.indexOf(S.skip);
    if (i < 0) { i = 0; }
    setSkip(SKIP_CHOICES[(i + step + SKIP_CHOICES.length) % SKIP_CHOICES.length]);
  }
  function openSkipMenu() {
    openMenu("Skip length", SKIP_CHOICES.map(function (v) {
      return { label: v + " seconds", selected: v === S.skip, onSelect: function () { setSkip(v); } };
    }));
  }

  /* ------------------------------------------------------------- transport */

  function renderPlayState() {
    var playing = !player.paused;
    var icon = playing ? ICONS.pause : ICONS.play;
    $("nl-play-icon").innerHTML = icon;
    $("nl-center-icon").innerHTML = icon;
    $("nl-play").setAttribute("aria-label", playing ? "Pause" : "Play");
    $("nl-center").setAttribute("aria-label", playing ? "Pause" : "Play");
    $("nl-center").style.display = playing ? "none" : "";
  }
  function togglePlay() {
    skips.flush();
    if (player.paused) {
      var d = duration();
      if (d && player.currentTime >= d - 0.5) { player.seek(0); }
      var p = player.play();
      if (p && p.catch) { p.catch(function () { /* blocked */ }); }
    } else {
      player.pause();
    }
  }
  // Quick presses group into one seek (SKIP_GROUP_MS after the last one): the native player flushes its decoders on every seek.
  var skips = X.skipper({
    position: function () { return player.currentTime; },
    duration: duration,
    seek: function (t) { player.seek(t); renderProgress(); },
    setTimeout: function (f, ms) { return setTimeout(f, ms); },
    clearTimeout: function (t) { clearTimeout(t); },
    ms: SKIP_GROUP_MS
  });
  function skipBy(sign) {
    var d = duration();
    var r = skips.press(sign * S.skip);
    toast((r.total < 0 ? "−" : "+") + Math.abs(r.total) + "s");
    renderProgress();
    if (remote && hasTrickplay() && d) { flashThumb(r.target, r.target / d); }   // P21: the remote sees where it lands
  }

  function renderProgress() {
    var d = duration();
    var pend = skips.pending();
    var cur = S.scrubbing ? Number($("nl-seek").value) : (pend !== null ? pend : player.currentTime);
    var seek = $("nl-seek");
    seek.max = d || 0;
    if (!S.scrubbing) { seek.value = cur; }
    seek.style.setProperty("--pct", d ? (cur / d * 100) + "%" : "0%");
    seek.style.setProperty("--buf", d ? Math.min(100, (cur + player.buffered) / d * 100) + "%" : "0%");
    $("nl-elapsed").textContent = fmt(cur);
    $("nl-remaining").textContent = "−" + fmt(Math.max(0, d - cur));
    $("nl-total").textContent = fmt(d);
    var ends = X.endsAt(Date.now(), d, cur, S.speed);
    $("nl-ends").hidden = ends === null;
    if (ends !== null) { $("nl-ends").textContent = "Ends at " + new Date(ends).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }); }
    if (d !== S.ticksFor) { renderTicks(); }
  }

  /* --------------------------------- thumbnails, chapters, skip intro, speed (M7a) */

  function hasTrickplay() { return !!(S.extras && S.extras.trickplay); }
  function hasChapters() { return !!(S.extras && S.extras.chapters && S.extras.chapters.length); }

  function loadExtras() {
    S.extras = null;
    S.skipIntro = false;
    S.autoSkipped = {};
    S.segDismissed = {};
    S.segFocused = {};
    var key = ++S.extrasKey;
    hideSeg();
    hideThumb();
    renderExtras();
    var qs = S.sourceId ? "?source=" + encodeURIComponent(S.sourceId) : "";
    fetch("/api/nl/player-extras/" + encodeURIComponent(itemId) + qs).then(function (r) {
      return r.ok ? r.json() : null;
    }).then(function (x) {
      if (!x || key !== S.extrasKey) { return; }
      S.extras = x;
      S.skipIntro = !!x.skip_intro;
      renderExtras();
      checkSegments();
    }).catch(function () { /* the extras are optional: without them the player is just the player */ });
  }
  function renderExtras() {
    $("nl-chapters").hidden = !hasChapters();
    S.ticksFor = null;
    renderTicks();
  }
  function renderTicks() {
    var box = $("nl-ticks");
    var d = duration();
    S.ticksFor = d;
    box.innerHTML = "";
    if (!hasChapters() || !d) { return; }
    X.chapterTicks(S.extras.chapters, d).forEach(function (f) {
      var tick = document.createElement("i");
      tick.style.setProperty("--f", String(f));
      box.appendChild(tick);
    });
  }

  // P21: the bubble above the seek bar. `fraction` (0..1) is where the pointer or target is along the bar.
  function thumbWidth() { return bigScreen ? 240 : 168; }
  function showThumb(seconds, fraction) {
    var wrap = $("nl-seek-wrap");
    var bubble = $("nl-thumb");
    var img = $("nl-thumb-img");
    var tile = hasTrickplay() ? X.tileFor(S.extras.trickplay, seconds) : null;
    if (tile) {
      var scale = thumbWidth() / tile.width;
      img.className = "nl-thumb-img";
      img.style.width = Math.round(tile.width * scale) + "px";
      img.style.height = Math.round(tile.height * scale) + "px";
      img.style.backgroundImage = 'url("' + tile.url + '")';
      img.style.backgroundSize = Math.round(tile.sheetWidth * scale) + "px " + Math.round(tile.sheetHeight * scale) + "px";
      img.style.backgroundPosition = "-" + Math.round(tile.x * scale) + "px -" + Math.round(tile.y * scale) + "px";
    } else {
      img.className = "nl-thumb-img none";
    }
    var ci = hasChapters() ? X.chapterIndexAt(S.extras.chapters, seconds) : -1;
    var chapterEl = $("nl-thumb-chapter");
    chapterEl.hidden = ci < 0;
    if (ci >= 0) { chapterEl.textContent = S.extras.chapters[ci].name; }
    $("nl-thumb-time").textContent = fmt(seconds);
    bubble.hidden = false;
    var w = wrap.clientWidth;
    var half = bubble.offsetWidth / 2;
    bubble.style.left = Math.max(half, Math.min(w - half, fraction * w)) + "px";
  }
  function hideThumb() {
    clearTimeout(S.thumbTimer);
    $("nl-thumb").hidden = true;
  }
  function flashThumb(seconds, fraction) {
    showThumb(seconds, fraction);
    clearTimeout(S.thumbTimer);
    S.thumbTimer = setTimeout(hideThumb, 1200);
  }

  // P22: the chapter list.
  function openChaptersMenu() {
    if (!hasChapters()) { return; }
    var chapters = S.extras.chapters;
    var cur = X.chapterIndexAt(chapters, player.currentTime || 0);
    openMenu("Chapters", chapters.map(function (c, i) {
      return { label: fmt(c.start_seconds) + "  " + c.name, selected: i === cur, onSelect: function () {
        player.seek(c.start_seconds);
        toast(c.name);
        renderProgress();
      } };
    }));
  }

  /* ------------------------------------------- picture size and quality (M10c: P18, P19, P20) */

  var FIT_LABELS = { fit: "Fit", fill: "Fill", original: "Original size" };
  var FIT_ORDER = ["fit", "fill", "original"];
  function applyFit() {
    // The Android app draws the picture itself behind this page, so the page can't resize it there.
    FIT_ORDER.forEach(function (f) { root.classList.toggle("fit-" + f, !isNative && S.fit === f); });
  }
  function setFit(fit, announce) {
    S.fit = FIT_ORDER.indexOf(fit) >= 0 ? fit : "fit";
    applyFit();
    if (announce) { toast("Picture: " + FIT_LABELS[S.fit]); }
  }
  function cycleFit() { setFit(FIT_ORDER[(FIT_ORDER.indexOf(S.fit) + 1) % FIT_ORDER.length], false); }

  function qualityInfo() { return (S.stream && S.stream.quality) || {}; }
  function qualityLabelFor(kbps) {
    var opts = qualityInfo().options || [];
    for (var i = 0; i < opts.length; i++) { if ((opts[i].kbps || null) === (kbps || null)) { return opts[i].label; } }
    return kbps ? kbps / 1000 + " Mbps" : "Auto (original quality)";
  }
  function setQuality(kbps) {
    var now = qualityInfo().applied_kbps || null;
    if ((kbps || null) === now) { return; }
    S.quality = kbps || 0;
    S.convertNoted = "";   // a new choice may need a new note
    toast("Quality: " + qualityLabelFor(kbps));
    reloadStream({});      // same place, same tracks, new stream
  }
  function openQualityMenu() {
    var q = qualityInfo();
    var applied = q.applied_kbps || null;
    var dflt = q.default_kbps || null;
    var items = [];
    var note = q.source_kbps ? "This file is about " + (q.source_kbps >= 1000 ? (q.source_kbps / 1000).toFixed(1) + " Mbps" : q.source_kbps + " kbps") + ". A lower choice makes the server convert it." : "A lower choice makes the server convert the picture to fit.";
    items.push({ kind: "note", text: note });
    (q.options || []).forEach(function (o) {
      items.push({
        label: o.label + (dflt && o.kbps === dflt ? " (your default)" : ""),
        selected: (o.kbps || null) === applied,
        onSelect: function () { setQuality(o.kbps); }
      });
    });
    openMenu("Quality", items);
  }
  function conversionText() {
    var c = (S.stream && S.stream.conversion) || null;
    if (!c || c.state !== "converting") { return "Playing as it is: no conversion."; }
    return c.note + (c.detail ? " (" + c.detail + ")" : "");
  }
  function openPictureMenu() {
    if (!S.stream) { return; }
    var items = [{ kind: "note", text: conversionText() }];
    if (!isNative) {
      items.push({ kind: "cycle", getLabel: function () { return "Picture size: " + FIT_LABELS[S.fit]; }, onSelect: cycleFit });
    }
    items.push({ kind: "action", label: "Quality: " + qualityLabelFor(qualityInfo().applied_kbps || null) + " …", onSelect: openQualityMenu });
    if (isNative && player.canShowStats) {
      items.push({ kind: "action", label: "Playback info…", onSelect: openInfoPanel });
    }
    openMenu("Picture", items);
  }

  // TV (bridge v6): what the app is doing, in plain words, refreshed once a second while the panel is open.
  // It is a menu like the others (D-pad focus on Close, Back closes) and the film keeps playing under it.
  var infoTimer = null;
  function stopInfoPanel() {
    if (infoTimer) { clearInterval(infoTimer); infoTimer = null; }
    menu.classList.remove("nl-menu-info");
  }
  function infoItems() {
    return X.infoLines(player.stats()).map(function (text) { return { kind: "note", text: text }; });
  }
  function refreshInfoPanel() {
    if (!S.menuOpen || !menu.classList.contains("nl-menu-info")) { stopInfoPanel(); return; }
    var lines = X.infoLines(player.stats());
    var notes = menu.querySelectorAll(".nl-menu-note");
    if (notes.length === lines.length) {
      lines.forEach(function (text, i) { if (notes[i].textContent !== text) { notes[i].textContent = text; } });
      return;
    }
    var anchor = menu.querySelector("button");   // the lines changed in number: rebuild them above Close
    Array.prototype.forEach.call(notes, function (n) { n.parentNode.removeChild(n); });
    lines.forEach(function (text) {
      var n = document.createElement("div");
      n.className = "nl-menu-note";
      n.textContent = text;
      menu.insertBefore(n, anchor);
    });
  }
  function openInfoPanel() {
    openMenu("Playback info", infoItems().concat([{ kind: "action", label: "Close", onSelect: function () { /* closeMenu did it */ } }]));
    menu.classList.add("nl-menu-info");
    infoTimer = setInterval(refreshInfoPanel, 1000);
  }
  // A short, friendly line when Jellyfin is converting the stream (once per film, or again after a quality change).
  function noteConversion(stream) {
    var c = stream && stream.conversion;
    if (!c || c.state !== "converting" || !c.note || S.convertNoted === c.note) { return; }
    S.convertNoted = c.note;
    toast(c.note, 6000);
  }

  // P17: speed.
  function renderSpeed() {
    $("nl-speed").hidden = !player.canChangeSpeed;
    $("nl-speed").textContent = X.speedLabel(S.speed);
    $("nl-speed").setAttribute("aria-label", "Playback speed " + X.speedLabel(S.speed));
  }
  function applySpeed() {
    if (player.canChangeSpeed) { player.playbackRate = S.speed; }
    renderSpeed();
  }
  function setSpeed(rate, announce) {
    S.speed = rate;
    applySpeed();
    if (announce) { toast("Speed " + X.speedLabel(rate)); }
  }
  function stepSpeed(step) {
    if (!player.canChangeSpeed) { return; }
    var next = X.nextSpeed(S.speed, step);
    if (next !== S.speed) { setSpeed(next, true); } else { toast("Speed " + X.speedLabel(S.speed)); }
  }
  function openSpeedMenu() {
    if (!player.canChangeSpeed) { return; }
    var items = X.SPEEDS.map(function (r) {
      return { label: X.speedLabel(r) + (r === 1 ? " (normal)" : ""), selected: r === S.speed, onSelect: function () { setSpeed(r, true); } };
    });
    if (introSegment() && S.extras.is_show) {
      items.push({ kind: "cycle", getLabel: function () { return "Always skip intro: " + (S.skipIntro ? "on" : "off"); }, onSelect: toggleSkipIntro });
    }
    openMenu("Playback", items);
  }

  // P23: "Skip intro" inside an Intro, "Next episode" inside an Outro.
  function introSegment() {
    var segs = (S.extras && S.extras.segments) || [];
    for (var i = 0; i < segs.length; i++) { if (segs[i].type === "intro") { return segs[i]; } }
    return null;
  }
  function segKey(seg) { return seg.type + "@" + seg.start_seconds; }
  function segVisible() { return !$("nl-seg").hidden; }
  function hideSeg() {
    var el = $("nl-seg");
    if (el.hidden) { return; }
    el.hidden = true;
    S.segCur = null;
    if (el.contains(document.activeElement)) { document.activeElement.blur(); }
  }
  function renderAlways() {
    var b = $("nl-seg-always");
    b.textContent = "Always skip intro: " + (S.skipIntro ? "on" : "off");
    b.setAttribute("aria-pressed", S.skipIntro ? "true" : "false");
  }
  function showSeg(seg, nextEpisode) {
    var el = $("nl-seg");
    var key = segKey(seg);
    $("nl-seg-go").textContent = nextEpisode ? "Next episode" : "Skip intro";
    var always = !nextEpisode && !!(S.extras && S.extras.is_show);
    $("nl-seg-always").hidden = !always;
    if (always) { renderAlways(); }
    el.hidden = false;
    S.segCur = { seg: seg, next: nextEpisode };
    // The remote needs somewhere to land the first time the button appears: OK then skips.
    if (remote && !S.segFocused[key] && !screenOpen() && !S.menuOpen && !nextCardVisible() &&
        !$("nl-controls").contains(document.activeElement)) {
      S.segFocused[key] = true;
      $("nl-seg-go").focus();
    }
  }
  function checkSegments() {
    var segs = S.extras && S.extras.segments;
    if (!segs || !segs.length || S.advancing || S.switching) { hideSeg(); return; }
    var t = player.currentTime || 0;
    var intro = X.segmentAt(segs, "intro", t);
    if (intro) {
      var key = segKey(intro);
      if (S.skipIntro && S.extras.is_show && !S.autoSkipped[key]) {
        S.autoSkipped[key] = true;
        hideSeg();
        player.seek(intro.end_seconds);
        toast("Intro skipped");
        renderProgress();
        return;
      }
      if (S.segDismissed[key]) { hideSeg(); } else { showSeg(intro, false); }
      return;
    }
    var outro = S.next ? X.segmentAt(segs, "outro", t) : null;
    if (outro && !nextCardVisible() && !S.nextCancelled && !S.segDismissed[segKey(outro)]) { showSeg(outro, true); return; }
    hideSeg();
  }
  function useSeg() {
    var cur = S.segCur;
    if (!cur) { return; }
    S.autoSkipped[segKey(cur.seg)] = true;
    if (cur.next) {
      hideSeg();
      advanceToNext();
    } else {
      hideSeg();
      player.seek(cur.seg.end_seconds);
      toast("Intro skipped");
      renderProgress();
    }
  }
  function dismissSeg() {
    if (S.segCur) { S.segDismissed[segKey(S.segCur.seg)] = true; }
    hideSeg();
  }
  function toggleSkipIntro() {
    var value = !S.skipIntro;
    S.skipIntro = value;
    renderAlways();
    toast(value ? "This show’s intro will be skipped" : "This show’s intro will play");
    fetch("/api/nl/show/" + encodeURIComponent(itemId) + "/skip-intro", {
      method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ skip_intro: value })
    }).then(function (r) {
      if (!r.ok) { throw new Error("not saved"); }
    }).catch(function () {
      S.skipIntro = !value;
      renderAlways();
      toast("Couldn’t save that");
    });
    if (value) { checkSegments(); }
  }

  /* ------------------------------------------------------ progress to server */

  function currentBody(event) {
    var st = S.stream;
    return {
      jellyfin_id: st.jellyfin_id,
      play_session_id: st.play_session_id || "",
      media_source_id: st.media_source_id || "",
      position_seconds: Math.max(0, player.currentTime || 0),
      paused: player.paused,
      event: event,
      play_method: st.play_method || "DirectPlay",
      // M9: which title this is and how far it got, so the server can tell "finished" (seen together).
      media_item_id: Number(itemId) || undefined,
      source_id: S.sourceId || undefined,
      duration_seconds: duration() || undefined,
      ended: !!S.ended
    };
  }
  function report(event, beacon) {
    if (!S.stream) { return; }
    var json = JSON.stringify(currentBody(event));
    try {
      if (beacon && navigator.sendBeacon) {
        navigator.sendBeacon("/api/nl/progress", new Blob([json], { type: "application/json" }));
        return;
      }
      fetch("/api/nl/progress", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: json, keepalive: true
      }).catch(function () { /* progress is best effort */ });
    } catch (e) { /* ignore */ }
  }
  function stopReporting(beacon) {
    clearInterval(S.progressTimer);
    S.progressTimer = null;
    if (S.started && !S.stopped) {
      report("stop", beacon);
      S.stopped = true;
    }
  }
  function onPlaying() {
    S.errors = 0;
    $("nl-buffering").hidden = true;
    if (!S.started) {
      S.started = true;
      S.stopped = false;
      report("start");
      watchSession.start(itemId);
    } else if (S.stopped) {
      S.stopped = false;
      report("start");
    }
    // The native player reports "progress" itself every 10 s (both bridge versions); the web player is polled here.
    if (!S.progressTimer && !isNative) {
      S.progressTimer = setInterval(function () { if (!player.paused) { report("progress"); } }, PROGRESS_EVERY_MS);
    }
  }

  /* ----------------------------------------------------------------- tracks */

  function textTrackList() {
    return ((S.stream && S.stream.subtitle_tracks) || []).filter(function (t) { return t.kind === "text" && t.url; });
  }
  function applyTextTracks() {
    player.setTextTracks(textTrackList().map(function (t) {
      return { id: t.index, label: t.label, language: t.language, url: t.url };
    }));
    player.showTextTrack(S.textIndex);
  }
  function subtitleChoices() {
    return [{ index: -1, label: "Off" }].concat((S.stream && S.stream.subtitle_tracks) || []);
  }
  function currentSubtitleIndex() {
    if (S.burnIndex >= 0) { return S.burnIndex; }
    return S.textIndex === null ? -1 : S.textIndex;
  }

  // The track picked here comes back the next time this movie or show plays (services/nl_track_memory.py).
  // track null = subtitles off. Best effort: a failed save only means the next play starts with the defaults.
  function rememberTrack(axis, track) {
    if (!window.fetch || !S.stream) { return; }
    var body = {};
    if (S.sourceId) { body.source = S.sourceId; }
    body[axis] = track ? {
      index: track.index, language: track.language || "", label: track.label || "", codec: track.codec || "",
      is_forced: !!track.is_forced, is_hearing_impaired: !!track.is_hearing_impaired,
      picture: !!(track.picture || track.kind === "image")
    } : { off: true };
    fetch("/api/nl/tracks/" + encodeURIComponent(itemId), {
      method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body)
    }).catch(function () { /* offline: nothing to remember */ });
  }

  function chooseSubtitle(index) {
    var track = (S.stream.subtitle_tracks || []).filter(function (t) { return t.index === index; })[0];
    if (index === -1 || !track) {
      rememberTrack("subtitle", null);
      S.textIndex = null;
      player.showTextTrack(null);
      if (S.burnIndex >= 0) { reloadStream({ subtitle: -1 }); }
      toast("Subtitles off");
    } else if (track.kind === "embedded") {
      // A picture subtitle inside the file: the TV app draws it (bridge v6); nothing is burned in.
      if (!player.canDrawEmbeddedSubtitles) { toast("This subtitle needs the newest TV app"); return; }
      rememberTrack("subtitle", track);
      S.textIndex = index;
      player.showTextTrack(index);
      toast("Subtitles: " + track.label);
      if (S.burnIndex >= 0) { reloadStream({ subtitle: -1 }); }
    } else if (track.kind === "image" && isLaptop && !S.allowConversion && !casting()) {
      // Burning a picture subtitle in re-encodes the video: the laptop asks first instead of doing it quietly.
      openMenu("Picture subtitle", [
        { kind: "note", text: "This subtitle is a picture. Showing it needs a converted copy (lower quality)." },
        { kind: "action", label: "Play a converted copy", onSelect: function () {
          S.allowConversion = true;
          chooseSubtitle(index);
        } },
        { kind: "action", label: "Keep the original", onSelect: function () { /* closeMenu did it; nothing changes */ } }
      ]);
    } else if (track.kind === "image") {
      S.textIndex = null;
      rememberTrack("subtitle", track);
      player.showTextTrack(null);
      toast("Subtitles: " + track.label + " (loading)");
      reloadStream({ subtitle: index });
    } else {
      rememberTrack("subtitle", track);
      S.textIndex = index;
      player.showTextTrack(index);
      toast("Subtitles: " + track.label);
      if (S.burnIndex >= 0) { reloadStream({ subtitle: -1 }); }
    }
  }
  function cycleSubtitle() {
    var all = subtitleChoices();
    if (all.length < 2) { toast("No subtitles"); return; }
    var cur = currentSubtitleIndex();
    var pos = 0;
    all.forEach(function (t, i) { if (t.index === cur) { pos = i; } });
    chooseSubtitle(all[(pos + 1) % all.length].index);
  }
  function openAudioMenu() {
    var tracks = (S.stream && S.stream.audio_tracks) || [];
    openMenu("Audio", tracks.map(function (t) {
      return { label: t.label, selected: t.index === S.audioIndex, onSelect: function () {
        if (t.index === S.audioIndex) { return; }
        rememberTrack("audio", t);
        toast("Audio: " + t.label);
        // The TV plays the original file and switches track inside it (bridge v5); everything else gets a new stream.
        var order = (S.stream.audio_tracks || []).indexOf(t);
        if (isNative && player.canSwitchAudioInPlace && S.stream.kind === "file" && typeof S.stream.audio_order === "number" && order >= 0) {
          S.audioIndex = t.index;
          S.stream.audio_index = t.index;
          S.stream.audio_order = order;
          player.setAudioOrder(order);
          return;
        }
        reloadStream({ audio: t.index });
      } };
    }));
  }
  // TV: the subtitle menus and the finder pause the film while they are open, and play it again when the last one
  // closes (only if it was playing). Moving from one subtitle menu to the next keeps it paused: the release waits a tick.
  var subHold = { active: false, resume: false, timer: null };
  function holdForSubtitles() {
    if (!remote || casting()) { return; }
    clearTimeout(subHold.timer);
    if (subHold.active) { return; }
    subHold.active = true;
    subHold.resume = !player.paused;
    if (subHold.resume) { player.pause(); }
  }
  function releaseSubtitleHold() {
    if (!subHold.active) { return; }
    clearTimeout(subHold.timer);
    subHold.timer = setTimeout(function () {
      if (S.menuOpen || S.finderOpen) { return; }
      subHold.active = false;
      if (subHold.resume && player.paused) { playNow(); }
    }, 0);
  }
  function openSubtitleMenu() {
    holdForSubtitles();
    var cur = currentSubtitleIndex();
    var items = subtitleChoices().map(function (t) {
      return { label: t.label, selected: t.index === cur, onSelect: function () { chooseSubtitle(t.index); } };
    });
    // Settings first: a film can carry dozens of tracks, and the list would push it off the bottom.
    if (player.canStyleSubtitles || player.canDelaySubtitles) {
      items.unshift({ kind: "action", top: true, label: "Subtitle settings…", onSelect: openSubtitleSettings });
    }
    items.push({ kind: "action", label: "Find subtitles…", onSelect: openFinder });
    openMenu("Subtitles", items);
  }

  /* ----------------------------------------------- subtitle look and delay (P15, P16) */

  function formatDelay(seconds) {
    var r = Math.round(seconds * 100) / 100;
    if (r === 0) { return "0 s"; }
    return (r > 0 ? "+" : "−") + Math.abs(r).toFixed(2) + " s";
  }
  function adoptSubStyle(saved) {
    if (saved && typeof saved === "object") {
      if (SUB_SIZES.indexOf(saved.size) >= 0) { S.subStyle.size = saved.size; }
      if (SUB_BACKGROUNDS.some(function (b) { return b[0] === saved.background; })) { S.subStyle.background = saved.background; }
      if (saved.raise === 0 || saved.raise === 1 || saved.raise === 2) { S.subStyle.raise = saved.raise; }
    }
  }
  function loadSubStyle() {
    // The person's saved look (the server knows who is watching); the device's own value shows until it arrives.
    try { adoptSubStyle(JSON.parse(lsGet("nl.subStyle") || "null")); } catch (e) { /* unreadable: keep the defaults */ }
    if (window.NLPrefs) {
      window.NLPrefs.get().then(function (p) {
        if (p && p.sub_style) { adoptSubStyle(p.sub_style); applySubStyle(); }
      });
    }
  }
  function applySubStyle() {
    if (player.canStyleSubtitles) { player.setSubtitleStyle(S.subStyle); }
  }
  function saveSubStyle() {
    var style = { size: S.subStyle.size, background: S.subStyle.background, raise: S.subStyle.raise };
    if (window.NLPrefs) { window.NLPrefs.save({ sub_style: style }); } else { lsSet("nl.subStyle", JSON.stringify(style)); }
    applySubStyle();
  }
  function backgroundName() {
    return SUB_BACKGROUNDS.filter(function (b) { return b[0] === S.subStyle.background; })[0][1];
  }
  function changeDelay(sign) {
    var next = Math.round((S.subDelay + sign * DELAY_STEP) * 100) / 100;
    S.subDelay = Math.max(-DELAY_MAX, Math.min(DELAY_MAX, next));
    player.setSubtitleDelay(S.subDelay);
  }
  function openSubtitleSettings() {
    holdForSubtitles();
    var items = [];
    if (player.canStyleSubtitles) {
      items.push({ kind: "cycle", getLabel: function () { return "Size: " + S.subStyle.size; }, onSelect: function () {
        S.subStyle.size = SUB_SIZES[(SUB_SIZES.indexOf(S.subStyle.size) + 1) % SUB_SIZES.length];
        saveSubStyle();
      } });
      items.push({ kind: "cycle", getLabel: function () { return "Background: " + backgroundName(); }, onSelect: function () {
        var i = 0;
        SUB_BACKGROUNDS.forEach(function (b, n) { if (b[0] === S.subStyle.background) { i = n; } });
        S.subStyle.background = SUB_BACKGROUNDS[(i + 1) % SUB_BACKGROUNDS.length][0];
        saveSubStyle();
      } });
      items.push({ kind: "cycle", getLabel: function () { return "Position: " + SUB_POSITIONS[S.subStyle.raise]; }, onSelect: function () {
        S.subStyle.raise = (S.subStyle.raise + 1) % SUB_POSITIONS.length;
        saveSubStyle();
      } });
    }
    if (player.canDelaySubtitles) {
      items.push({ kind: "stepper", name: "Delay", stepText: DELAY_STEP + " seconds",
        getLabel: function () { return "Delay: " + formatDelay(S.subDelay); },
        onStep: changeDelay });
    }
    openMenu("Subtitle settings", items);
  }

  /* ------------------------------------------------- find subtitles (D1, D2, D3) */

  function openFinder() {
    if (!S.stream) { return; }
    holdForSubtitles();
    S.finderOpen = true;
    NLSubtitles.open({
      container: $("nl-subfind"),
      itemId: itemId,
      sourceId: S.sourceId,
      tv: bigScreen,
      onDownloaded: addDownloadedSubtitle,
      onClose: function () {
        S.finderOpen = false;
        releaseSubtitleHold();
        showControls();
        if (remote) { $("nl-subs").focus(); }
      }
    });
  }

  function textTracksOf(stream) {
    return (stream.subtitle_tracks || []).filter(function (t) { return t.kind === "text" && t.url; });
  }
  function wait(ms) { return new Promise(function (resolve) { setTimeout(resolve, ms); }); }

  // After Jellyfin saved a subtitle it takes a moment to list it: ask a few times for a text track we didn't have.
  function findNewTrack(before, language, pos, attempt) {
    return fetchPlay({ audio: S.audioIndex, subtitle: S.burnIndex >= 0 ? -1 : null, start: pos }).then(function (stream) {
      var added = textTracksOf(stream).filter(function (t) { return before.indexOf(t.index) < 0; })[0];
      if (!added && attempt + 1 >= SUB_POLL_TRIES) {
        // Same language saved over an older file of ours: use that external track.
        added = textTracksOf(stream).filter(function (t) { return t.is_external && t.language === language; }).pop();
      }
      if (added || attempt + 1 >= SUB_POLL_TRIES) { return { stream: stream, added: added || null }; }
      return wait(SUB_POLL_MS).then(function () { return findNewTrack(before, language, pos, attempt + 1); });
    });
  }

  // D3: the new track appears and is selected. On the web the video keeps playing (only the <track> elements are
  // rebuilt). The TV player only reads side-loaded tracks when playback starts, so there the stream is started
  // again at the same position with the new subtitle selected.
  function addDownloadedSubtitle(result) {
    var before = ((S.stream && S.stream.subtitle_tracks) || []).map(function (t) { return t.index; });
    var pos = player.currentTime || 0;
    var resume = !player.paused;
    return findNewTrack(before, (result && result.language) || "", pos, 0).then(function (found) {
      if (!found.added) {
        throw new Error("Saved, but Jellyfin hasn’t listed it yet. Look in the Subtitles menu in a moment.");
      }
      if (isNative || casting()) {   // the TV reads side-loaded tracks only when a stream starts
        S.textIndex = found.added.index;
        stopReporting(false);
        $("nl-buffering").hidden = false;
        applyStream(found.stream, pos);
        if (resume && !casting()) { playNow(); }
        toast("Subtitles: " + found.added.label);
        return;
      }
      S.stream.subtitle_tracks = found.stream.subtitle_tracks;
      renderTrackButtons();
      applyTextTracks();
      chooseSubtitle(found.added.index);
    });
  }

  /* ----------------------------------------------------------- stream loading */

  var capsParam = null;
  function capsString() {
    if (capsParam === null) {
      var c = NLPlayerCore.capabilities();
      capsParam = "v=" + c.video.join(".") + ";a=" + c.audio.join(".") + ";h=" + c.hls.join(".");
    }
    return capsParam;
  }

  function fetchPlay(params) {
    // The Android app decodes far more than a browser: it asks for the TV profile instead of sending caps.
    // While casting the Chromecast is what plays: the cast endpoint answers with the cast profile and LAN URLs.
    // The laptop app asks for the original video (client=laptop; its caps shape the profile) and, like the TV, can
    // agree to a converted copy. The device part of the query is NLPlayerExtras.playDeviceParams.
    var qs = NLPlayerExtras.playDeviceParams({
      casting: casting(), isNative: isNative, isLaptop: isLaptop, allowConversion: S.allowConversion,
      caps: casting() || isNative ? "" : capsString()
    });
    if (params.audio !== undefined && params.audio !== null) { qs.push("audio=" + encodeURIComponent(params.audio)); }
    if (params.subtitle !== undefined && params.subtitle !== null) { qs.push("subtitle=" + encodeURIComponent(params.subtitle)); }
    if (params.start > 0) { qs.push("start=" + encodeURIComponent(params.start.toFixed(1))); }
    if (S.sourceId) { qs.push("source=" + encodeURIComponent(S.sourceId)); }
    if (!casting() && S.quality !== null) { qs.push("max_kbps=" + encodeURIComponent(S.quality)); }
    var url = (casting() ? "/api/nl/cast/play/" : "/api/nl/play/") + encodeURIComponent(itemId) + (qs.length ? "?" + qs.join("&") : "");
    return fetch(url).then(function (resp) {
      return resp.json().catch(function () { return {}; }).then(function (body) {
        if (!resp.ok) {
          var err = new Error(body.detail || "Could not start playback.");
          err.detail = body.detail || "";
          err.canConvert = !!body.can_convert;
          err.unreachable = resp.status === 503 || resp.status === 502 || resp.status === 504;
          throw err;
        }
        return body;
      });
    }, function () {
      var err = new Error(UNREACHABLE);
      err.unreachable = true;
      throw err;
    });
  }

  function applyStream(stream, startAt) {
    // A stream fetched for the browser while a TV is connected (a lookup that was still in flight): fetch the TV's.
    if (casting() && !stream.cast) { S.stream = stream; castReload(startAt); return; }
    S.stream = stream;
    S.started = false;
    S.stopped = true;
    S.audioIndex = stream.audio_index;
    S.burnIndex = stream.subtitle_index >= 0 ? stream.subtitle_index : -1;
    renderTrackButtons();
    player.load({ url: stream.url, kind: stream.kind, startAt: startAt, native: isNative ? nativeMeta(stream) : undefined,
      cast: casting() ? stream : undefined });
    applyTextTracks();
    applySubStyle();
    if (S.subDelay) { player.setSubtitleDelay(S.subDelay); }   // the TV app starts every stream at 0
    applySpeed();
    applyFit();
    noteConversion(stream);
    if (isNative) { warmSubtitles(stream); }
  }
  // TV: the first time a subtitle inside the file is asked for, Jellyfin reads through the whole file to pull it out,
  // so switching to it took seconds and the picture stuttered. A while after the film starts, fetch each text track
  // once (one at a time) so Jellyfin has them ready and a switch is instant.
  var warmTimer = null;
  function warmSubtitles(stream) {
    clearTimeout(warmTimer);
    var urls = textTracksOf(stream).map(function (t) { return t.url; }).slice(0, SUB_WARM_MAX);
    if (!urls.length || !window.fetch) { return; }
    warmTimer = setTimeout(function next() {
      if (S.stream !== stream || !urls.length) { return; }
      fetch(urls.shift(), { credentials: "same-origin" }).then(function (r) { return r.text(); }, function () { /* offline */ })
        .then(function () { warmTimer = setTimeout(next, 500); });
    }, SUB_WARM_AFTER_MS);
  }
  // What the native player needs besides the stream (see the NorthernLightsNative contract in player-core.js).
  function nativeMeta(stream) {
    var epEl = $("nl-episode");
    return {
      audioOrder: typeof stream.audio_order === "number" ? stream.audio_order : null,
      title: stream.title || "",
      subtitle: epEl.hidden ? "" : epEl.textContent,
      skipSeconds: S.skip,
      audioTracks: stream.audio_tracks || [],
      subtitleTracks: stream.subtitle_tracks || [],
      audioIndex: stream.audio_index,
      subtitleIndex: stream.subtitle_index >= 0 ? stream.subtitle_index : -1,
      nextUp: S.next ? { label: nextLabel(S.next) } : null
    };
  }
  function renderTrackButtons() {
    $("nl-audio").hidden = ((S.stream.audio_tracks || []).length < 2);
    $("nl-subs").hidden = false;   // always there: "Find subtitles" lives in this menu
  }
  function playNow() {
    var p = player.play();
    if (p && p.catch) { p.catch(function () { renderPlayState(); showControls(); }); }
  }

  // Audio and burned-in subtitle changes need a new stream at the same position.
  function reloadStream(change) {
    if (!S.stream || S.switching) { return; }
    S.switching = true;
    var pos = player.currentTime || 0;
    var resume = !player.paused;
    stopReporting(false);
    $("nl-buffering").hidden = false;
    var params = {
      audio: change.audio !== undefined ? change.audio : S.audioIndex,
      // -1, not "the default": the page's own subtitle state is the truth here, not the remembered or preferred one.
      subtitle: change.subtitle !== undefined ? change.subtitle : (S.burnIndex >= 0 ? S.burnIndex : -1),
      start: pos
    };
    S.retry = function () { hideError(); reloadStream(change); };
    fetchPlay(params).then(function (stream) {
      S.switching = false;
      applyStream(stream, pos);
      if (resume) { playNow(); }
    }).catch(function (err) {
      S.switching = false;
      showError(err.unreachable ? UNREACHABLE : err.message, err.unreachable ? err.detail : "", err.canConvert);
    });
  }

  function startFirst(opts) {
    opts = opts || {};
    if (opts.fromNext) { S.allowConversion = false; S.subDelay = 0; player.setSubtitleDelay(0);   // the delay belongs to one file
      // Track numbers belong to one file too: the next episode starts with the tracks the server picks for it (the
      // ones remembered for the show, else the language prefs).
      S.textIndex = null;
      S.defaultSubDone = false;
    }
    hideError();
    $("nl-buffering").hidden = false;
    S.retry = function () { startFirst(opts); };
    fetchPlay({ start: 0 }).then(function (stream) {
      document.title = (stream.title ? stream.title + " - " : "") + "Northern Lights";
      $("nl-title").textContent = stream.title || "";
      if (stream.source_id) { S.sourceId = stream.media_type === "tv" ? stream.source_id : null; }
      S.backHref = PAGE_ROOT + (stream.media_type === "tv" ? "show/" : "title/") + encodeURIComponent(itemId);
      $("nl-error-back").setAttribute("href", S.backHref);
      var ep = stream.episode;
      if (ep && (ep.season || ep.episode || ep.name)) {
        var bits = [];
        if (ep.season != null) { bits.push("S" + ep.season); }
        if (ep.episode != null) { bits.push("E" + ep.episode); }
        if (ep.name) { bits.push(ep.name); }
        $("nl-episode").textContent = bits.join(" · ");
        $("nl-episode").hidden = false;
      } else {
        $("nl-episode").hidden = true;
      }
      S.advancing = false;
      loadNext(stream);
      loadExtras();
      $("nl-buffering").hidden = true;
      var resumeAt = stream.resume_seconds || 0;
      var note = opts.fromNext ? "" : (stream.resume_note || "");   // M4: "You're at 42:10, Sam is at 38:00"
      var choice = opts.fromNext ? "0" : urlResume;
      urlResume = null;                      // the link only decides the first load
      if (stream.together_prompt) {
        // M9a (U5): the show is "watch together" and one person is about to start the episode alone.
        var ask = stream.together_prompt;
        $("nl-resume-name").textContent = ask.text + (ask.note ? "\n" + ask.note : "");
        $("nl-resume-yes").textContent = ask.yes || "Watch anyway";
        $("nl-resume-no").textContent = ask.no || "Not now";
        $("nl-resume").hidden = false;
        showControls();
        $("nl-resume-yes").focus();
        $("nl-resume-yes").onclick = function () {
          $("nl-resume").hidden = true;
          stream.together_prompt = null;
          proceed(stream, choice, resumeAt, note);
        };
        $("nl-resume-no").onclick = function () {
          $("nl-resume").hidden = true;
          $("nl-resume-no").textContent = "Start over";
          leave();
        };
        return;
      }
      proceed(stream, choice, resumeAt, note);
    }).catch(function (err) {
      S.advancing = false;
      $("nl-buffering").hidden = true;
      showError(err.unreachable ? UNREACHABLE : err.message, err.unreachable ? err.detail : "", err.canConvert);
    });
  }
  // Starts a loaded stream: straight away when the link already chose, else after the Resume question.
  function proceed(stream, choice, resumeAt, note) {
    try {
      if (choice === "0") {
        begin(stream, 0);
      } else if (choice === "1" && resumeAt > 0 && !stream.played) {
        begin(stream, resumeAt);
      } else if (resumeAt > 30 && !stream.played) {
        $("nl-resume-name").textContent = (stream.title || "") + (note ? "\n" + note : "");
        note = "";
        $("nl-resume-yes").textContent = "Resume from " + fmt(resumeAt);
        $("nl-resume-no").textContent = "Start over";
        $("nl-resume").hidden = false;
        showControls();
        $("nl-resume-yes").focus();
        $("nl-resume-yes").onclick = function () { $("nl-resume").hidden = true; begin(stream, resumeAt); };
        $("nl-resume-no").onclick = function () { $("nl-resume").hidden = true; begin(stream, 0); };
      } else {
        begin(stream, 0);
      }
      if (note) { toast(note, 6000); }
    } catch (err) {
      showError(err.message);
    }
  }
  // The default track may be a disc (embedded) one, which only an app with bridge v6 can draw.
  function defaultSubtitleUsable(stream) {
    var t = (stream.subtitle_tracks || []).filter(function (x) { return x.index === stream.default_subtitle_index; })[0];
    return !!t && (t.kind !== "embedded" || !!player.canDrawEmbeddedSubtitles);
  }
  function begin(stream, at) {
    if (isNativeV1 && S.nextPromise) {
      // The old native player shows "Next: ..." from the label we hand it, so wait (briefly) for the lookup.
      var pending = S.nextPromise;
      S.nextPromise = null;
      Promise.race([pending, new Promise(function (resolve) { setTimeout(resolve, 1500); })]).then(function () { begin(stream, at); });
      return;
    }
    // M4: the person's own default subtitles (a text track, or on the TV a disc track the app draws; for the browser picture subtitles were burned in by the server).
    if (stream.default_subtitle_index >= 0 && !S.defaultSubDone && defaultSubtitleUsable(stream)) { S.textIndex = stream.default_subtitle_index; }
    S.defaultSubDone = true;
    applyStream(stream, at);
    if (!casting()) { playNow(); }   // the Chromecast starts by itself when the load request arrives
    showControls();
  }

  /* ------------------------------------------------- next episode (P11, P12) */

  // The episode after this one, from the server; the autoplay switch lives on the show page.
  function loadNext(stream) {
    S.next = null;
    S.nextCancelled = false;
    hideNextCard();
    S.nextPromise = null;
    if (stream.media_type !== "tv" || !stream.source_id) { return; }
    var forSource = stream.source_id;
    S.nextPromise = fetchNext(forSource).then(function (n) {
      if (n && n.has_next && S.sourceId === forSource) { S.next = n; }
      renderCast();
    }).catch(function () { /* no card without the lookup; the episode still plays */ });
  }
  function nextLabel(n) {
    var label = n.code ? n.code.replace(" · ", "·") : "";
    return "Next: " + (label ? label + " " : "") + (n.name || "").trim();
  }
  function fetchNext(sourceId) {
    return fetch("/api/nl/next/" + encodeURIComponent(itemId) + "?source=" + encodeURIComponent(sourceId))
      .then(function (r) { return r.ok ? r.json() : null; });
  }
  function nextCardVisible() { return !$("nl-next").hidden; }
  function hideNextCard() {
    $("nl-next").hidden = true;
    $("nl-next-count").textContent = "";
  }
  function showNextCard() {
    var n = S.next;
    if (!n) { return; }
    hideSeg();
    $("nl-next-title").textContent = nextLabel(n);
    $("nl-next").hidden = false;
    if (remote) { $("nl-next-play").focus(); }   // the remote needs somewhere to land: OK plays it, Back cancels
  }
  function advanceToNext() {
    var n = S.next;
    if (!n || S.advancing) { return; }
    S.advancing = true;
    hideNextCard();
    stopReporting(false);
    S.sourceId = n.source_id;
    S.next = null;
    try { window.history.replaceState(null, "", NLPlayerExtras.playUrl(surface, remote, itemId, n.source_id)); } catch (e) { /* ignore */ }
    startFirst({ fromNext: true });
  }
  function cancelNext() {
    S.nextCancelled = true;
    hideNextCard();
    toast("Autoplay cancelled");
  }
  // Called on every time update: card at 30 s before the end, countdown over the last 10 s.
  function checkNext() {
    // The old (v1) native player draws its own "Next" prompt and reports "ended"; the page only advances then.
    if (isNativeV1 || !S.next || S.nextCancelled || S.advancing || S.switching) { return; }
    var d = duration();
    if (!d || d < 2 * NEXT_CARD_SECONDS) { return; }
    var remaining = d - player.currentTime;
    if (remaining > NEXT_CARD_SECONDS + 0.5) {
      if (nextCardVisible()) { hideNextCard(); }   // seeked back out of the closing seconds
      return;
    }
    if (!nextCardVisible()) { showNextCard(); }
    if (!S.next.autoplay) { $("nl-next-count").textContent = ""; return; }
    if (remaining <= COUNTDOWN_SECONDS) {
      var secs = Math.max(0, Math.ceil(remaining - 0.05));
      $("nl-next-count").textContent = "Starting in " + secs;
      if (remaining <= 0.35) { advanceToNext(); }
    } else {
      $("nl-next-count").textContent = "";
    }
  }

  /* ------------------------------------------------------------------ errors */

  function showError(message, detail, canConvert) {
    stopReporting(false);
    $("nl-convert").hidden = !(canConvert && (isNative || isLaptop));
    // The server's reason goes on a second line, so a setup problem isn't mistaken for "mars is off".
    $("nl-error-text").textContent = detail ? message + "\n" + detail : message;
    $("nl-error").hidden = false;
    root.classList.add("controls-visible");
    $("nl-retry").focus();
  }
  function hideError() { $("nl-error").hidden = true; }
  // TV: the original file cannot be played; the viewer may ask for a converted (lower quality) copy instead.
  $("nl-convert").addEventListener("click", function () {
    S.allowConversion = true;
    $("nl-convert").hidden = true;
    if (S.retry) { S.retry(); }
  });

  player.on("error", function (e) {
    bufferSpinner.clear();
    if (S.switching || !S.stream) { return; }
    if (e && e.server) { // Jellyfin's transcode failed: reloading would fail the same way
      showError(CONVERT_FAILED, CONVERT_DETAIL);
      return;
    }
    S.errors += 1;
    if (S.errors <= 2) {
      reloadStream({});
    } else if (isNative && e && e.message) {
      // The TV player's own reason (a Media3 error code): mars answered, so "is mars on?" would send the viewer the wrong way.
      showError("The TV player stopped.", e.message);
    } else {
      showError(e && e.format ? unplayableText() : UNREACHABLE);
    }
  });

  /* ------------------------------------------------------------------ events */

  // A double-tap skip makes the video "play" again for a moment; that must not pop the controls up (P14).
  function showControlsUnlessQuiet() { if (Date.now() >= S.quietUntil) { showControls(); } }
  player.on("play", function () { renderPlayState(); showControlsUnlessQuiet(); });
  player.on("pause", function () {
    bufferSpinner.clear();
    renderPlayState();
    showControls();
    if (S.started && !S.stopped && !video.ended) { report("progress"); }
  });
  player.on("playing", function () { bufferSpinner.clear(); renderPlayState(); onPlaying(); showControlsUnlessQuiet(); });
  var bufferSpinner = X.spinner({
    show: function () { $("nl-buffering").hidden = false; },
    hide: function () { $("nl-buffering").hidden = true; },
    setTimeout: function (f, ms) { return setTimeout(f, ms); },
    clearTimeout: function (t) { clearTimeout(t); },
    ms: SPINNER_AFTER_MS
  });
  player.on("waiting", function () { bufferSpinner.wait(); });
  player.on("loadedmetadata", function () { renderProgress(); renderTicks(); });
  player.on("timeupdate", function () { renderProgress(); checkNext(); checkSegments(); });
  player.on("seeked", function () {
    bufferSpinner.clear();
    if (S.started && !S.stopped) { report("progress"); }
  });
  player.on("ended", function () {
    bufferSpinner.clear();
    renderPlayState();
    showControls();
    S.ended = true;
    stopReporting(false);
    S.ended = false;
    if (S.next && S.next.autoplay && !S.nextCancelled) { advanceToNext(); }
    else if (!S.next) { watchSession.finish(); askReaction(); }   // M9c: after the film, or the last episode, "How was it?"
  });

  /* --------------------------------------------- after finishing: how was it (M9c, W10) */

  // The answer becomes one of the existing verdicts for whoever is watching (server side, /api/nl/react).
  function reactOpen() { return !$("nl-react").hidden; }
  function closeReaction() {
    $("nl-react").hidden = true;
    showControls();
  }
  function askReaction() {
    if (casting() || S.reactAsked === itemId + ":" + S.sourceId) { return; }
    S.reactAsked = itemId + ":" + S.sourceId;
    fetch("/api/nl/react/" + encodeURIComponent(itemId)).then(function (r) { return r.ok ? r.json() : null; }).then(function (q) {
      if (!q || !q.ask || !(q.options || []).length) { return; }
      $("nl-react-text").textContent = q.text || "How was it?";
      var box = $("nl-react-options");
      box.textContent = "";
      q.options.forEach(function (o) {
        var b = document.createElement("button");
        b.type = "button";
        b.className = "nl-primary";
        b.textContent = o.label;
        b.addEventListener("click", function () {
          box.querySelectorAll("button").forEach(function (x) { x.disabled = true; });
          fetch("/api/nl/react/" + encodeURIComponent(itemId), {
            method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ reaction: o.id })
          }).then(function (r) { toast(r.ok ? "Thanks, saved" : "Couldn\u2019t save that"); }).catch(function () { toast("Couldn\u2019t save that"); });
          closeReaction();
        });
        box.appendChild(b);
      });
      $("nl-react").hidden = false;
      showControls();
      var first = box.querySelector("button");
      if (first) { first.focus(); }
    }).catch(function () { /* no question without the lookup */ });
  }
  $("nl-react-skip").addEventListener("click", closeReaction);
  // Events only the native adapter emits (the Android app's player, see player-core.js).
  player.on("progress", function () { if (S.started && !S.stopped && !player.paused) { report("progress"); } });
  player.on("stopped", leave);   // Back in the native player: leave like the web player's back button
  player.on("audiochange", function (e) {
    if (e && typeof e.index === "number" && e.index !== S.audioIndex) {
      rememberTrack("audio", ((S.stream && S.stream.audio_tracks) || []).filter(function (t) { return t.index === e.index; })[0] || null);
      reloadStream({ audio: e.index });
    }
  });
  player.on("subtitlechange", function (e) {
    var index = e && typeof e.index === "number" ? e.index : -1;
    if (index !== currentSubtitleIndex()) { chooseSubtitle(index); }
  });
  function renderVolume() {
    $("nl-volume").value = player.muted ? 0 : player.volume;
    $("nl-mute-icon").innerHTML = (player.muted || player.volume === 0) ? ICONS.muted : ICONS.vol;
    $("nl-mute").setAttribute("aria-label", player.muted ? "Unmute" : "Mute");
  }
  player.on("volumechange", renderVolume);

  function leave(completed) {
    skips.cancel();
    stopReporting(true);
    if (completed) { watchSession.finish(); } else { watchSession.cancel(); }
    if (casting()) { castKit.cast.end(true); }   // leaving the player stops the TV too (no remote page to control it)
    // On the TV, go back through the history when we came from one of its screens, so Back
    // from the title page does not land on the player again. Otherwise replace this page.
    if (bigScreen) {
      var cameFromTv = false;
      try {
        var ref = document.referrer ? new URL(document.referrer) : null;
        cameFromTv = !!ref && ref.origin === window.location.origin && ref.pathname.indexOf(PAGE_ROOT) === 0;
      } catch (e) { /* no referrer */ }
      if (cameFromTv && window.history.length > 1) { window.history.back(); } else { window.location.replace(S.backHref); }
      return;
    }
    window.location.href = S.backHref;
  }
  window.addEventListener("pagehide", function () {
    if (casting()) { if (S.started && !S.stopped) { report("progress", true); } } else {
      stopReporting(true);
      watchSession.cancel();
    }   // the TV keeps playing
    if (isNative) { player.destroy(); }   // stop the native player when the page goes away
  });
  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "hidden") {
      if (!casting()) { stopReporting(true); }   // a locked phone is still the remote: progress keeps going
    } else if (S.started && S.stopped && !video.ended && !player.paused) {
      S.stopped = false;
      report("start");
      if (!S.progressTimer && !isNative) {
        S.progressTimer = setInterval(function () { if (!player.paused) { report("progress"); } }, PROGRESS_EVERY_MS);
      }
    }
  });

  /* ---------------------------------------------------------------- wiring UI */

  $("nl-back").addEventListener("click", leave);
  $("nl-error-back").addEventListener("click", function (e) { e.preventDefault(); leave(); });
  $("nl-next-play").addEventListener("click", advanceToNext);
  $("nl-next-cancel").addEventListener("click", cancelNext);
  $("nl-retry").addEventListener("click", function () { S.errors = 0; if (S.retry) { S.retry(); } });
  $("nl-play").addEventListener("click", togglePlay);
  $("nl-center").addEventListener("click", togglePlay);
  $("nl-skip-back").addEventListener("click", function () { skipBy(-1); });
  $("nl-skip-fwd").addEventListener("click", function () { skipBy(1); });
  $("nl-audio").addEventListener("click", function () { S.menuOpen ? closeMenu() : openAudioMenu(); });
  $("nl-subs").addEventListener("click", function () { S.menuOpen ? closeMenu() : openSubtitleMenu(); });
  $("nl-fs").addEventListener("click", function () { player.toggleFullscreen(root); });
  $("nl-mute").addEventListener("click", toggleMute);
  $("nl-chapters").addEventListener("click", function () { S.menuOpen ? closeMenu() : openChaptersMenu(); });
  $("nl-speed").addEventListener("click", function () { S.menuOpen ? closeMenu() : openSpeedMenu(); });
  $("nl-picture").addEventListener("click", function () { S.menuOpen ? closeMenu() : openPictureMenu(); });
  $("nl-seg-go").addEventListener("click", useSeg);
  $("nl-seg-always").addEventListener("click", toggleSkipIntro);

  var chip = $("nl-skip-chip");
  var pressTimer = null;
  var longPressed = false;
  chip.addEventListener("pointerdown", function () {
    longPressed = false;
    clearTimeout(pressTimer);
    pressTimer = setTimeout(function () { longPressed = true; openSkipMenu(); }, LONG_PRESS_MS);
  });
  ["pointerup", "pointerleave", "pointercancel"].forEach(function (n) {
    chip.addEventListener(n, function () { clearTimeout(pressTimer); });
  });
  chip.addEventListener("click", function () {
    if (longPressed) { longPressed = false; return; }
    cycleSkip(1);
  });
  chip.addEventListener("contextmenu", function (e) { e.preventDefault(); openSkipMenu(); });

  var seek = $("nl-seek");
  seek.addEventListener("input", function () {
    skips.cancel();   // the bar was dragged: the dragged position wins
    S.scrubbing = true;
    renderProgress();
    var d = duration();
    if (d) { showThumb(Number(seek.value), Number(seek.value) / d); }   // P21: scrubbing shows where you are
  });
  seek.addEventListener("change", function () {
    player.seek(Number(seek.value));
    S.scrubbing = false;
    hideThumb();
    renderProgress();
    showControls();
  });
  // P21: hovering the bar (mouse) shows the frame under the pointer.
  seek.addEventListener("pointermove", function (e) {
    if (e.pointerType === "touch" || S.scrubbing) { return; }
    var d = duration();
    if (!d) { return; }
    var r = seek.getBoundingClientRect();
    if (!(r.width > 16)) { return; }
    var f = Math.max(0, Math.min(1, (e.clientX - r.left - 8) / (r.width - 16)));
    showThumb(f * d, f);
  });
  seek.addEventListener("pointerleave", function () { if (!S.scrubbing) { hideThumb(); } });

  var vol = $("nl-volume");
  vol.addEventListener("input", function () {
    player.muted = false;
    player.volume = Number(vol.value);
    saveVolume();
  });
  function saveVolume() { lsSet("nl.volume", player.volume); lsSet("nl.muted", player.muted ? "1" : "0"); }
  function toggleMute() { player.muted = !player.muted; saveVolume(); toast(player.muted ? "Muted" : "Sound on"); }
  function nudgeVolume(delta) {
    player.muted = false;
    player.volume = Math.round((player.volume + delta) * 100) / 100;
    saveVolume();
    toast("Volume " + Math.round(player.volume * 100) + "%");
  }

  // Mouse: clicking the picture pauses/plays, double-click is fullscreen (unchanged).
  // Touch (P14, phones; not on the TV): a tap shows or hides the controls, a double tap on the left / right half
  // skips back / ahead by the skip length, a vertical swipe on the right half changes the volume where the browser
  // lets a page do that. Play/pause is the big button and the bottom one.
  var G = { down: null, lastTap: null, tapTimer: null };
  function touchGestures() { return S.pointerType === "touch" && !remote; }
  function onPicture(e) { return e.target === video || e.target.id === "nl-controls"; }
  root.addEventListener("pointerdown", function (e) {
    S.pointerType = e.pointerType || "mouse";
    S.wasVisible = controlsVisible();
    var ignore = S.closedMenuByTap;
    S.closedMenuByTap = false;
    if (touchGestures() && onPicture(e)) {
      var r = root.getBoundingClientRect();
      G.down = { id: e.pointerId, x: e.clientX, y: e.clientY, half: X.halfOf(e.clientX - r.left, r.width), height: r.height,
        volume: player.volume, mode: null, ignore: ignore };
    } else {
      G.down = null;
      showControls();
    }
  }, true);
  root.addEventListener("pointermove", function (e) {
    var g = G.down;
    if (!g) { if (e.pointerType !== "touch") { showControls(); } return; }
    if (e.pointerId !== g.id) { return; }
    var dx = e.clientX - g.x, dy = e.clientY - g.y;
    if (!g.mode && X.isVerticalSwipe(dx, dy)) { g.mode = g.half === "right" && player.canSetVolume ? "volume" : "ignored"; }
    if (g.mode === "volume") {
      player.muted = false;
      player.volume = X.swipeVolume(g.volume, g.y, e.clientY, g.height);
      toast("Volume " + Math.round(player.volume * 100) + "%", 700);
    }
  });
  function endGesture(e, cancelled) {
    var g = G.down;
    if (!g || e.pointerId !== g.id) { return; }
    G.down = null;
    if (cancelled || g.ignore) { return; }
    if (g.mode === "volume") { saveVolume(); return; }
    if (g.mode || Math.abs(e.clientX - g.x) > 12 || Math.abs(e.clientY - g.y) > 12) { return; }   // a drag, not a tap
    var action = X.tapAction(G.lastTap, Date.now(), g.half);
    clearTimeout(G.tapTimer);
    if (action === "first") {
      G.lastTap = { at: Date.now(), half: g.half };
      G.tapTimer = setTimeout(function () {
        G.lastTap = null;
        if (S.menuOpen) { return; }
        if (controlsVisible()) { hideControlsNow(); } else { showControls(); }
      }, X.DOUBLE_TAP_MS);
    } else {
      G.lastTap = null;
      S.quietUntil = Date.now() + 1500;
      skipBy(action === "double-left" ? -1 : 1);
    }
  }
  root.addEventListener("pointerup", function (e) { endGesture(e, false); });
  root.addEventListener("pointercancel", function (e) { endGesture(e, true); });
  root.addEventListener("click", function (e) {
    if (!onPicture(e)) { return; }
    if (S.menuOpen) { return; }
    if (touchGestures()) { return; }   // taps are handled by the pointer events above
    if (e.pointerType === "touch" && !S.wasVisible) { return; }
    togglePlay();
  });
  root.addEventListener("dblclick", function (e) {
    if (touchGestures()) { return; }
    if (onPicture(e)) { player.toggleFullscreen(root); }
  });
  player.on("fullscreenchange", showControls);

  /* --------------------------------------------------------------- keyboard */

  function closeHelp() { $("nl-help").hidden = true; showControls(); }

  /* ---- remote control (TV): arrows move focus, OK clicks, Back leaves. Returns true when handled. */

  function visibleIn(container, selector) {
    return Array.prototype.slice.call(container.querySelectorAll(selector)).filter(function (b) {
      return !b.hidden && !b.closest("[hidden]") && (b.offsetWidth || b.offsetHeight);
    });
  }
  function stepFocus(list, step) {
    if (!list.length) { return; }
    var i = list.indexOf(document.activeElement);
    var next = i < 0 ? 0 : Math.max(0, Math.min(list.length - 1, i + step));
    list[next].focus();
  }
  function isBackKey(e) {
    var k = e.key, c = e.keyCode;
    return k === "Escape" || k === "Backspace" || k === "GoBack" || k === "BrowserBack" || k === "XF86Back" ||
      c === 4 || c === 27 || c === 8 || c === 461 || c === 10009;
  }
  function isOkKey(e) { return e.key === "Enter" || e.keyCode === 23 || e.keyCode === 66 || e.keyCode === 13; }

  // Play/pause/seek keys on the remote (the Android WebView passes them on as these key names).
  function mediaKey(e) {
    var k = e.key;
    if (k === "MediaPlayPause") { togglePlay(); }
    else if (k === "MediaPlay") { if (player.paused) { togglePlay(); } }
    else if (k === "MediaPause") { if (!player.paused) { togglePlay(); } }
    else if (k === "MediaFastForward" || k === "MediaTrackNext") { skipBy(1); }
    else if (k === "MediaRewind" || k === "MediaTrackPrevious") { skipBy(-1); }
    else { return false; }
    e.preventDefault();
    showControls();
    return true;
  }

  function tvKey(e) {
    var dir = { ArrowLeft: "left", ArrowRight: "right", ArrowUp: "up", ArrowDown: "down" }[e.key];
    var back = isBackKey(e);
    var ok = isOkKey(e);
    if (!dir && !back && !ok) { return false; }
    e.preventDefault();
    if (e.repeat && (back || ok)) { return true; }
    if (back || ok) { skips.flush(); }   // a pending skip lands before OK / Back acts

    // Back: close what is open; then hide the controls; then "press Back again" before leaving the film.
    if (back) {
      if (S.menuOpen) { closeMenu(); } else if (reactOpen()) { closeReaction(); } else if (nextCardVisible()) { cancelNext(); } else if (segVisible()) { dismissSeg(); }
      else if (!$("nl-error").hidden || !$("nl-resume").hidden) { leave(); }   // nothing playing to keep
      else {
        var action = X.backAction(controlsVisible(), S.backArmedAt, Date.now());
        if (action === "hide") { hideControlsNow(); S.backArmedAt = 0; }
        else if (action === "leave") { leave(); }
        else { S.backArmedAt = Date.now(); toast("Press Back again to stop watching", X.BACK_AGAIN_MS); }
      }
      return true;
    }
    if (S.backArmedAt) { S.backArmedAt = 0; $("nl-toast").classList.remove("on"); }   // another key: no pending leave
    showControls();

    // The skip / next-episode button has the remote: OK uses it, up/down reach the "always" switch, left/right still
    // skip like on the picture.
    if (segVisible() && $("nl-seg").contains(document.activeElement) && !S.menuOpen && !screenOpen() && !nextCardVisible()) {
      var segButtons = visibleIn($("nl-seg"), "button");
      if (ok) { document.activeElement.click(); }
      else if (dir === "left") { skipBy(-1); }
      else if (dir === "right") { skipBy(1); }
      else { stepFocus(segButtons, dir === "up" ? -1 : 1); }
      return true;
    }

    // A menu, the resume question, the error card or the next-episode card: arrows move between its buttons.
    var screen = null;
    if (S.menuOpen) { screen = menu; }
    else if (!$("nl-resume").hidden) { screen = $("nl-resume"); }
    else if (reactOpen()) { screen = $("nl-react"); }
    else if (!$("nl-error").hidden) { screen = $("nl-error"); }
    else if (nextCardVisible()) { screen = $("nl-next"); }
    if (screen) {
      var buttons = visibleIn(screen, "button, a[href]");
      var active = document.activeElement;
      if (buttons.indexOf(active) < 0) { if (buttons[0]) { buttons[0].focus(); } return true; }
      if (ok) { active.click(); } else { stepFocus(buttons, (dir === "left" || dir === "up") ? -1 : 1); }
      return true;
    }

    var controls = $("nl-controls");
    var focused = document.activeElement;
    var inControls = focused !== document.body && controls.contains(focused);

    // Controls hidden or not focused: the picture is the target. Left/right skip, OK pauses, up/down open the controls.
    if (!inControls) {
      if (ok) { togglePlay(); }
      else if (dir === "left") { skipBy(-1); }
      else if (dir === "right") { skipBy(1); }
      else { $("nl-play").focus(); }
      return true;
    }

    // Focus is on the controls: three rows, back button / seek bar / button row.
    var rows = [[$("nl-back")], [$("nl-seek")], visibleIn($("nl-controls").querySelector(".nl-button-row"), "button")];
    var ri = -1;
    rows.forEach(function (r, i) { if (r.indexOf(focused) >= 0) { ri = i; } });
    if (ri < 0) { $("nl-play").focus(); return true; }
    if (ok) {
      if (focused === $("nl-seek")) { togglePlay(); } else { focused.click(); }
    } else if (dir === "left" || dir === "right") {
      if (focused === $("nl-seek")) { skipBy(dir === "left" ? -1 : 1); } else { stepFocus(rows[ri], dir === "left" ? -1 : 1); }
    } else if (dir === "up") {
      if (ri > 0) { (ri === 2 ? $("nl-seek") : $("nl-back")).focus(); }
    } else if (ri === 0) {
      $("nl-seek").focus();
    } else if (ri === 1) {
      $("nl-play").focus();
    } else {
      focused.blur();   // down from the buttons: back to the picture
    }
    return true;
  }

  document.addEventListener("keydown", function (e) {
    if (e.ctrlKey || e.metaKey || e.altKey) { return; }
    if (!$("nl-help").hidden) { e.preventDefault(); closeHelp(); return; }
    if (remote && mediaKey(e)) { return; }
    if (remote && tvKey(e)) { return; }
    var key = e.key;
    if (key === "Escape") {
      if (S.menuOpen) { e.preventDefault(); closeMenu(); } else if (reactOpen()) { e.preventDefault(); closeReaction(); } else if (nextCardVisible()) { e.preventDefault(); cancelNext(); }
      else if (bigScreen && !document.fullscreenElement && !S.finderOpen) { e.preventDefault(); leave(); }   // laptop app: Escape goes back like the TV's Back
      return;
    }
    if (!$("nl-resume").hidden || reactOpen() || !$("nl-error").hidden) { showControls(); return; }
    showControls();
    var handled = true;
    switch (key) {
      case " ": case "k": case "K": togglePlay(); break;
      case "ArrowLeft": case "j": case "J": skipBy(-1); break;
      case "ArrowRight": case "l": case "L": skipBy(1); break;
      case "ArrowUp": nudgeVolume(0.1); break;
      case "ArrowDown": nudgeVolume(-0.1); break;
      case "m": case "M": toggleMute(); break;
      case "f": case "F": player.toggleFullscreen(root); break;
      case "c": case "C": cycleSubtitle(); break;
      case "n": case "N": if (nextCardVisible()) { advanceToNext(); } else { handled = false; } break;
      case "[": cycleSkip(-1); break;
      case "]": cycleSkip(1); break;
      case "<": stepSpeed(-1); break;
      case ">": stepSpeed(1); break;
      case "?": $("nl-help").hidden = false; break;
      default: handled = false;
    }
    if (handled) { e.preventDefault(); }
  });
  // A focused button would also "click" on Space keyup; we already handled it on keydown.
  document.addEventListener("keyup", function (e) {
    if (e.key === " " && ($("nl-help").hidden)) { e.preventDefault(); }
  });

  /* ------------------------------------------------------------------ casting (M8) */
  /* While a Chromecast is connected, `player` is the TV (cast.js). Everything above keeps working against it: play,
   * pause, skip by the skip length, seek, audio and subtitle tracks (a new stream at the same position), next episode,
   * volume. Progress still goes to /api/nl/progress, so the server reports it to Jellyfin for the viewer(s). */

  var castBtn = $("nl-cast");
  var castPanel = $("nl-cast-panel");

  function renderCast() {
    if (!castKit) { return; }
    var state = castKit.cast.state;
    var on = casting();
    castBtn.hidden = !(on || state === "ready" || state === "connecting" || state === "connected");
    castBtn.classList.toggle("on", on);
    castBtn.setAttribute("aria-label", on ? "Casting to " + castKit.cast.deviceName : "Cast to a TV");
    castBtn.setAttribute("aria-pressed", on ? "true" : "false");
    root.classList.toggle("casting", on);
    $("nl-picture").hidden = on;   // picture size and quality are for the screen you are holding
    castPanel.hidden = !on;
    if (on) {
      $("nl-cast-device").textContent = "Casting to " + castKit.cast.deviceName;
      $("nl-cast-next").hidden = !S.next;
      root.classList.add("controls-visible");
      clearTimeout(S.hideTimer);
    }
  }
  // Speed, subtitle look and the volume slider belong to whichever player is in charge.
  function refreshPlayerUi() {
    renderSpeed();
    applySubStyle();
    renderVolume();
    renderPlayState();
    renderProgress();
  }

  // The stream for the TV at `pos`: the cast profile, LAN URLs, a token. Used when casting starts and after
  // an audio or picture-subtitle change.
  function castReload(pos) {
    if (!casting()) { return; }
    S.switching = true;
    $("nl-buffering").hidden = false;
    S.retry = function () { hideError(); castReload(pos); };
    fetchPlay({ audio: S.audioIndex, subtitle: S.burnIndex >= 0 ? S.burnIndex : null, start: pos }).then(function (stream) {
      S.switching = false;
      if (casting()) { applyStream(stream, pos); }
    }).catch(function (err) {
      S.switching = false;
      $("nl-buffering").hidden = true;
      toast(err.unreachable ? UNREACHABLE : err.message, 5000);
      castKit.cast.end(true);   // back to this screen, with the "Continue here" card
    });
  }

  function startCasting(ev) {
    if (!castKit || casting()) { return; }
    var pos;
    if (ev.resumed) {
      // This tab was reloaded while the TV is playing: take the remote back only for the same title.
      if (!ev.item || String(ev.item.item_id) !== String(itemId) || (urlSource && Number(ev.item.source_id) !== urlSource)) {
        renderCast();
        return;
      }
      pos = ev.position || 0;
    } else {
      pos = localPlayer.currentTime || 0;
    }
    stopReporting(false);          // the browser's own Jellyfin play session ends here
    localPlayer.pause();
    castKit.activate();
    S.errors = 0;
    renderCast();
    refreshPlayerUi();
    if (S.stream) { castReload(pos); }   // else: the first stream is still being fetched; applyStream hands it over
  }

  // C4: the TV session ended (Stop, the TV's own button, or the phone left it). Put the picture back on this
  // screen at the TV's position and offer to carry on here.
  function endCasting() {
    if (!casting()) { renderCast(); return; }
    var finished = castKit.remote.lastState.ended;
    var pos = player.currentTime || 0;
    stopReporting(false);          // "stop" at the TV's position, still with the cast play session
    castKit.deactivate();
    S.switching = false;
    hideNextCard();
    renderCast();
    refreshPlayerUi();
    if (finished) { leave(true); return; }
    S.retry = function () { handBack(pos); };
    handBack(pos);
  }

  function handBack(pos) {
    hideError();
    $("nl-buffering").hidden = false;
    fetchPlay({ audio: S.audioIndex, subtitle: S.burnIndex >= 0 ? S.burnIndex : null, start: pos }).then(function (stream) {
      $("nl-buffering").hidden = true;
      applyStream(stream, pos);   // loaded and paused: "Continue here" only has to press play
      $("nl-resume-name").textContent = "Casting stopped";
      $("nl-resume-yes").textContent = "Continue here from " + fmt(pos);
      $("nl-resume-no").textContent = "Back to the list";
      $("nl-resume").hidden = false;
      showControls();
      $("nl-resume-yes").focus();
      $("nl-resume-yes").onclick = function () { $("nl-resume").hidden = true; $("nl-resume-no").textContent = "Start over"; playNow(); };
      $("nl-resume-no").onclick = function () { $("nl-resume").hidden = true; $("nl-resume-no").textContent = "Start over"; leave(); };
    }).catch(function (err) {
      $("nl-buffering").hidden = true;
      showError(err.unreachable ? UNREACHABLE : err.message, err.unreachable ? err.detail : "");
    });
  }

  function stopCasting() { if (casting()) { castKit.cast.end(true); } }

  if (castKit) {
    castKit.cast.on("state", renderCast);
    castKit.cast.on("connected", startCasting);
    castKit.cast.on("ended", endCasting);
    castBtn.addEventListener("click", function () {
      if (casting()) {
        S.menuOpen ? closeMenu() : openMenu("Casting to " + castKit.cast.deviceName, [
          { kind: "action", label: "Stop casting", onSelect: stopCasting }
        ]);
      } else {
        castKit.cast.request();
      }
    });
    $("nl-cast-stop").addEventListener("click", stopCasting);
    $("nl-cast-next").addEventListener("click", advanceToNext);
    fetch("/api/nl/cast/config").then(function (r) { return r.ok ? r.json() : null; }).then(function (cfg) {
      if (cfg) { castKit.cast.init(cfg); }
    }).catch(function () { /* no casting without the config */ });
  }

  /* -------------------------------------------------------------------- init */

  var savedSkip = Number(lsGet("nl.skipSeconds"));
  S.skip = SKIP_CHOICES.indexOf(savedSkip) >= 0 ? savedSkip : 10;
  // M10c, H5: the person's defaults (skip length, picture size) are read before the first stream starts. Quality and
  // autoplay defaults are applied by the server. A change in the player is for this film only.
  function applyPrefs(prefs) {
    if (prefs && SKIP_CHOICES.indexOf(prefs.skip_seconds) >= 0) { S.skip = prefs.skip_seconds; }
    if (prefs && FIT_ORDER.indexOf(prefs.picture_fit) >= 0) { S.fit = prefs.picture_fit; }
    renderSkip();
    applyFit();
  }
  var savedVol = parseFloat(lsGet("nl.volume"));
  if (isFinite(savedVol)) { player.volume = savedVol; }
  player.muted = lsGet("nl.muted") === "1";
  renderSkip();
  renderSpeed();
  loadSubStyle();
  applySubStyle();
  renderPlayState();
  renderProgress();
  $("nl-mute-icon").innerHTML = ICONS.vol;
  if (window.NLPrefs) {
    window.NLPrefs.get().then(applyPrefs, function () {}).then(function () { startFirst(); });
  } else {
    startFirst();
  }
})();
