/* Northern Lights player UI (M1).
 * Everything that touches the video goes through the adapter in player-core.js. */
(function () {
  "use strict";

  var SKIP_CHOICES = [10, 15, 30, 60];
  var HIDE_AFTER_MS = 3000;
  var PROGRESS_EVERY_MS = 10000;
  var LONG_PRESS_MS = 500;
  var UNREACHABLE = "Can’t reach the movie server. Is mars on?";
  var CONVERT_FAILED = "The server couldn’t convert this video.";
  var CONVERT_DETAIL = "Jellyfin failed to transcode it. Check Jellyfin’s FFmpeg log.";
  var UNPLAYABLE = "This video’s format can’t play in this browser.";

  function $(id) { return document.getElementById(id); }
  var root = $("nl-player");
  var video = $("nl-video");
  var itemId = document.body.getAttribute("data-item-id");
  var player = NLPlayerCore.create(video);

  var S = {
    stream: null,
    skip: 10,
    audioIndex: null,
    burnIndex: -1,        // picture subtitle currently burned in by Jellyfin, -1 = none
    textIndex: null,      // text subtitle currently shown, null = none
    started: false,       // "start" sent for the current stream
    stopped: true,        // "stop" sent (or nothing started yet)
    watchSent: false,
    scrubbing: false,
    wasVisible: true,
    errors: 0,
    switching: false,
    retry: null,
    progressTimer: null,
    hideTimer: null,
    toastTimer: null,
    menuOpen: false
  };

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

  function toast(text) {
    var el = $("nl-toast");
    el.textContent = text;
    el.classList.add("on");
    clearTimeout(S.toastTimer);
    S.toastTimer = setTimeout(function () { el.classList.remove("on"); }, 1100);
  }

  /* -------------------------------------------------------- control hiding */

  function screenOpen() {
    return !$("nl-resume").hidden || !$("nl-error").hidden || !$("nl-help").hidden;
  }
  function canHide() {
    return !player.paused && !S.menuOpen && !S.scrubbing && !screenOpen();
  }
  function showControls() {
    root.classList.add("controls-visible");
    clearTimeout(S.hideTimer);
    if (canHide()) {
      S.hideTimer = setTimeout(function () {
        if (canHide()) { root.classList.remove("controls-visible"); }
      }, HIDE_AFTER_MS);
    }
  }
  function controlsVisible() { return root.classList.contains("controls-visible"); }

  /* ------------------------------------------------------------------ menus */

  var menu = $("nl-menu");
  function closeMenu() {
    menu.hidden = true;
    menu.innerHTML = "";
    S.menuOpen = false;
    showControls();
  }
  function openMenu(title, items) {
    menu.innerHTML = "";
    var h = document.createElement("div");
    h.className = "nl-menu-title";
    h.textContent = title;
    menu.appendChild(h);
    items.forEach(function (it) {
      var b = document.createElement("button");
      b.type = "button";
      b.setAttribute("role", "menuitemradio");
      b.setAttribute("aria-checked", it.selected ? "true" : "false");
      var tick = document.createElement("span");
      tick.className = "tick";
      tick.textContent = it.selected ? "✓" : "";
      var label = document.createElement("span");
      label.textContent = it.label;
      b.appendChild(tick);
      b.appendChild(label);
      b.addEventListener("click", function () { closeMenu(); it.onSelect(); });
      menu.appendChild(b);
    });
    menu.hidden = false;
    S.menuOpen = true;
    showControls();
    var first = menu.querySelector("button[aria-checked='true']") || menu.querySelector("button");
    if (first) { first.focus(); }
  }
  document.addEventListener("pointerdown", function (e) {
    if (S.menuOpen && !menu.contains(e.target) && !e.target.closest(".nl-text-btn, .nl-chip")) { closeMenu(); }
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
    if (player.paused) {
      var d = duration();
      if (d && player.currentTime >= d - 0.5) { player.seek(0); }
      var p = player.play();
      if (p && p.catch) { p.catch(function () { /* blocked */ }); }
    } else {
      player.pause();
    }
  }
  function skipBy(sign) {
    var d = duration();
    var t = player.currentTime + sign * S.skip;
    t = Math.max(0, d ? Math.min(t, d - 0.1) : t);
    player.seek(t);
    toast((sign < 0 ? "−" : "+") + S.skip + "s");
    renderProgress();
  }

  function renderProgress() {
    var d = duration();
    var cur = S.scrubbing ? Number($("nl-seek").value) : player.currentTime;
    var seek = $("nl-seek");
    seek.max = d || 0;
    if (!S.scrubbing) { seek.value = cur; }
    seek.style.setProperty("--pct", d ? (cur / d * 100) + "%" : "0%");
    seek.style.setProperty("--buf", d ? Math.min(100, (cur + player.buffered) / d * 100) + "%" : "0%");
    $("nl-elapsed").textContent = fmt(cur);
    $("nl-remaining").textContent = "−" + fmt(Math.max(0, d - cur));
    $("nl-total").textContent = fmt(d);
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
      play_method: st.play_method || "DirectPlay"
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
      if (!S.watchSent) {
        S.watchSent = true;
        fetch("/api/watch/start", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ media_item_id: Number(itemId) })
        }).catch(function () { /* recommender history is best effort */ });
      }
    } else if (S.stopped) {
      S.stopped = false;
      report("start");
    }
    if (!S.progressTimer) {
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

  function chooseSubtitle(index) {
    var track = (S.stream.subtitle_tracks || []).filter(function (t) { return t.index === index; })[0];
    if (index === -1 || !track) {
      S.textIndex = null;
      player.showTextTrack(null);
      if (S.burnIndex >= 0) { reloadStream({ subtitle: -1 }); }
      toast("Subtitles off");
    } else if (track.kind === "image") {
      S.textIndex = null;
      player.showTextTrack(null);
      toast("Subtitles: " + track.label + " (loading)");
      reloadStream({ subtitle: index });
    } else {
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
        toast("Audio: " + t.label);
        reloadStream({ audio: t.index });
      } };
    }));
  }
  function openSubtitleMenu() {
    var cur = currentSubtitleIndex();
    openMenu("Subtitles", subtitleChoices().map(function (t) {
      return { label: t.label, selected: t.index === cur, onSelect: function () { chooseSubtitle(t.index); } };
    }));
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
    var qs = ["caps=" + encodeURIComponent(capsString())];
    if (params.audio !== undefined && params.audio !== null) { qs.push("audio=" + encodeURIComponent(params.audio)); }
    if (params.subtitle !== undefined && params.subtitle !== null) { qs.push("subtitle=" + encodeURIComponent(params.subtitle)); }
    if (params.start > 0) { qs.push("start=" + encodeURIComponent(params.start.toFixed(1))); }
    var url = "/api/nl/play/" + encodeURIComponent(itemId) + (qs.length ? "?" + qs.join("&") : "");
    return fetch(url).then(function (resp) {
      return resp.json().catch(function () { return {}; }).then(function (body) {
        if (!resp.ok) {
          var err = new Error(body.detail || "Could not start playback.");
          err.detail = body.detail || "";
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
    S.stream = stream;
    S.started = false;
    S.stopped = true;
    S.audioIndex = stream.audio_index;
    S.burnIndex = stream.subtitle_index >= 0 ? stream.subtitle_index : -1;
    renderTrackButtons();
    player.load({ url: stream.url, kind: stream.kind, startAt: startAt });
    applyTextTracks();
  }
  function renderTrackButtons() {
    $("nl-audio").hidden = ((S.stream.audio_tracks || []).length < 2);
    $("nl-subs").hidden = ((S.stream.subtitle_tracks || []).length < 1);
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
      subtitle: change.subtitle !== undefined ? change.subtitle : (S.burnIndex >= 0 ? S.burnIndex : null),
      start: pos
    };
    S.retry = function () { hideError(); reloadStream(change); };
    fetchPlay(params).then(function (stream) {
      S.switching = false;
      applyStream(stream, pos);
      if (resume) { playNow(); }
    }).catch(function (err) {
      S.switching = false;
      showError(err.unreachable ? UNREACHABLE : err.message, err.unreachable ? err.detail : "");
    });
  }

  function startFirst() {
    hideError();
    $("nl-buffering").hidden = false;
    S.retry = startFirst;
    fetchPlay({ start: 0 }).then(function (stream) {
      document.title = (stream.title ? stream.title + " - " : "") + "Northern Lights";
      $("nl-title").textContent = stream.title || "";
      var ep = stream.episode;
      if (ep && (ep.season || ep.episode || ep.name)) {
        var bits = [];
        if (ep.season != null) { bits.push("S" + ep.season); }
        if (ep.episode != null) { bits.push("E" + ep.episode); }
        if (ep.name) { bits.push(ep.name); }
        $("nl-episode").textContent = bits.join(" · ");
        $("nl-episode").hidden = false;
      }
      $("nl-buffering").hidden = true;
      var resumeAt = stream.resume_seconds || 0;
      if (resumeAt > 30 && !stream.played) {
        $("nl-resume-name").textContent = stream.title || "";
        $("nl-resume-yes").textContent = "Resume from " + fmt(resumeAt);
        $("nl-resume").hidden = false;
        showControls();
        $("nl-resume-yes").focus();
        $("nl-resume-yes").onclick = function () { $("nl-resume").hidden = true; begin(stream, resumeAt); };
        $("nl-resume-no").onclick = function () { $("nl-resume").hidden = true; begin(stream, 0); };
      } else {
        begin(stream, 0);
      }
    }).catch(function (err) {
      $("nl-buffering").hidden = true;
      showError(err.unreachable ? UNREACHABLE : err.message, err.unreachable ? err.detail : "");
    });
  }
  function begin(stream, at) {
    applyStream(stream, at);
    playNow();
    showControls();
  }

  /* ------------------------------------------------------------------ errors */

  function showError(message, detail) {
    stopReporting(false);
    // The server's reason goes on a second line, so a setup problem isn't mistaken for "mars is off".
    $("nl-error-text").textContent = detail ? message + "\n" + detail : message;
    $("nl-error").hidden = false;
    root.classList.add("controls-visible");
    $("nl-retry").focus();
  }
  function hideError() { $("nl-error").hidden = true; }

  player.on("error", function (e) {
    if (S.switching || !S.stream) { return; }
    if (e && e.server) { // Jellyfin's transcode failed: reloading would fail the same way
      showError(CONVERT_FAILED, CONVERT_DETAIL);
      return;
    }
    S.errors += 1;
    if (S.errors <= 2) {
      reloadStream({});
    } else {
      showError(e && e.format ? UNPLAYABLE : UNREACHABLE);
    }
  });

  /* ------------------------------------------------------------------ events */

  player.on("play", function () { renderPlayState(); showControls(); });
  player.on("pause", function () {
    renderPlayState();
    showControls();
    if (S.started && !S.stopped && !video.ended) { report("progress"); }
  });
  player.on("playing", function () { renderPlayState(); onPlaying(); showControls(); });
  player.on("waiting", function () { $("nl-buffering").hidden = false; });
  player.on("loadedmetadata", function () { renderProgress(); });
  player.on("timeupdate", renderProgress);
  player.on("seeked", function () {
    $("nl-buffering").hidden = true;
    if (S.started && !S.stopped) { report("progress"); }
  });
  player.on("ended", function () {
    renderPlayState();
    showControls();
    stopReporting(false);
  });
  player.on("volumechange", function () {
    $("nl-volume").value = player.muted ? 0 : player.volume;
    $("nl-mute-icon").innerHTML = (player.muted || player.volume === 0) ? ICONS.muted : ICONS.vol;
    $("nl-mute").setAttribute("aria-label", player.muted ? "Unmute" : "Mute");
  });

  function leave() {
    stopReporting(true);
    window.location.href = "/nl/";
  }
  window.addEventListener("pagehide", function () { stopReporting(true); });
  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "hidden") {
      stopReporting(true);
    } else if (S.started && S.stopped && !video.ended && !player.paused) {
      S.stopped = false;
      report("start");
      if (!S.progressTimer) {
        S.progressTimer = setInterval(function () { if (!player.paused) { report("progress"); } }, PROGRESS_EVERY_MS);
      }
    }
  });

  /* ---------------------------------------------------------------- wiring UI */

  $("nl-back").addEventListener("click", leave);
  $("nl-error-back").addEventListener("click", function (e) { e.preventDefault(); leave(); });
  $("nl-retry").addEventListener("click", function () { S.errors = 0; if (S.retry) { S.retry(); } });
  $("nl-play").addEventListener("click", togglePlay);
  $("nl-center").addEventListener("click", togglePlay);
  $("nl-skip-back").addEventListener("click", function () { skipBy(-1); });
  $("nl-skip-fwd").addEventListener("click", function () { skipBy(1); });
  $("nl-audio").addEventListener("click", function () { S.menuOpen ? closeMenu() : openAudioMenu(); });
  $("nl-subs").addEventListener("click", function () { S.menuOpen ? closeMenu() : openSubtitleMenu(); });
  $("nl-fs").addEventListener("click", function () { player.toggleFullscreen(root); });
  $("nl-mute").addEventListener("click", toggleMute);

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
  seek.addEventListener("input", function () { S.scrubbing = true; renderProgress(); });
  seek.addEventListener("change", function () {
    player.seek(Number(seek.value));
    S.scrubbing = false;
    renderProgress();
    showControls();
  });

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

  // Tapping the picture: pause/play. On a touch screen with hidden controls, the first tap only shows them.
  root.addEventListener("pointerdown", function () { S.wasVisible = controlsVisible(); showControls(); }, true);
  root.addEventListener("pointermove", showControls);
  root.addEventListener("click", function (e) {
    if (e.target !== video && e.target.id !== "nl-controls") { return; }
    if (S.menuOpen) { return; }
    if (e.pointerType === "touch" && !S.wasVisible) { return; }
    togglePlay();
  });
  root.addEventListener("dblclick", function (e) {
    if (e.target === video || e.target.id === "nl-controls") { player.toggleFullscreen(root); }
  });
  player.on("fullscreenchange", showControls);

  /* --------------------------------------------------------------- keyboard */

  function closeHelp() { $("nl-help").hidden = true; showControls(); }
  document.addEventListener("keydown", function (e) {
    if (e.ctrlKey || e.metaKey || e.altKey) { return; }
    if (!$("nl-help").hidden) { e.preventDefault(); closeHelp(); return; }
    var key = e.key;
    if (key === "Escape") {
      if (S.menuOpen) { e.preventDefault(); closeMenu(); }
      return;
    }
    if (!$("nl-resume").hidden || !$("nl-error").hidden) { showControls(); return; }
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
      case "[": cycleSkip(-1); break;
      case "]": cycleSkip(1); break;
      case "?": $("nl-help").hidden = false; break;
      default: handled = false;
    }
    if (handled) { e.preventDefault(); }
  });
  // A focused button would also "click" on Space keyup; we already handled it on keydown.
  document.addEventListener("keyup", function (e) {
    if (e.key === " " && ($("nl-help").hidden)) { e.preventDefault(); }
  });

  /* -------------------------------------------------------------------- init */

  var savedSkip = Number(lsGet("nl.skipSeconds"));
  S.skip = SKIP_CHOICES.indexOf(savedSkip) >= 0 ? savedSkip : 10;
  var savedVol = parseFloat(lsGet("nl.volume"));
  if (isFinite(savedVol)) { player.volume = savedVol; }
  player.muted = lsGet("nl.muted") === "1";
  renderSkip();
  renderPlayState();
  renderProgress();
  $("nl-mute-icon").innerHTML = ICONS.vol;
  startFirst();
})();
