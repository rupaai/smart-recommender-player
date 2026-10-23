/* Northern Lights player adapter (S3).
 * The rest of the UI talks only to this interface; nothing else may touch hls.js or the <video>.
 *
 *   const p = NLPlayerCore.create(videoElement);
 *   p.load({url, kind, startAt});        // kind: "hls" | "file"; startAt in seconds (optional)
 *   p.play(); p.pause(); p.seek(seconds);
 *   p.currentTime; p.duration; p.paused; p.buffered (seconds buffered ahead of the playhead)
 *   p.volume (0..1, get/set); p.muted (get/set)
 *   p.setTextTracks([{id, label, language, url}])   // external WebVTT, all start hidden
 *   p.showTextTrack(id | null)                       // null = subtitles off
 *   p.setSubtitleDelay(seconds)                      // web: shifts the shown track's cue times; p.subtitleDelay reads it
 *   p.setSubtitleStyle({size: S|M|L|XL, background: none|shadow|box, raise: 0|1|2})
 *   p.canStyleSubtitles / p.canDelaySubtitles        // false where the player can't (the TV app has no delay: Media3 1.5.1
 *                                                    // has no subtitle offset; style needs bridge version 3)
 *   p.toggleFullscreen(containerElement); p.isFullscreen
 *   p.on("play"|"pause"|"timeupdate"|"ended"|"waiting"|"playing"|"loadedmetadata"|"seeking"|"seeked"
 *        |"volumechange"|"fullscreenchange"|"error", fn);
 *   p.destroy();
 *
 *   NLPlayerCore.capabilities(videoEl?) -> {video:[], audio:[], hls:[]}  codecs this browser can decode
 *   (video h264|hevc|av1|vp9, audio aac|mp3|opus|flac|ac3|eac3); the page sends it so Jellyfin
 *   copies what the device can play and only converts the rest.
 *   Errors: {format, server, message}; server = the stream's HTTP 5xx (Jellyfin failed to convert).
 *
 *   Inside the Android TV app (window.NorthernLightsNative exists) create() returns the native
 *   adapter instead (same surface, see "native adapter" below); load() then also takes src.native.
 *   NLPlayerCore.isNative() tells which one you get; NLPlayerCore.nativeVersion() is the bridge version
 *   ("3": + setSubtitleStyle; "2": pause/resume/seekTo/setSubtitle + "time" events, the web controls work; "1": old app).
 */
(function (global) {
  "use strict";

  var EVENTS = ["play", "pause", "timeupdate", "ended", "waiting", "playing", "loadedmetadata",
    "seeking", "seeked", "volumechange"];

  /* ------------------------------------------------------------- capability probe */

  var VIDEO_PROBES = [ // plain-file playback: [token, canPlayType string]
    ["h264", 'video/mp4; codecs="avc1.640028"'],
    ["hevc", 'video/mp4; codecs="hvc1.2.4.L153.B0"'],
    ["av1", 'video/mp4; codecs="av01.0.08M.10"'],
    ["vp9", 'video/webm; codecs="vp09.00.10.08"']
  ];
  var AUDIO_PROBES = [
    ["aac", 'audio/mp4; codecs="mp4a.40.2"'],
    ["mp3", 'audio/mpeg'],
    ["opus", 'audio/mp4; codecs="opus"'],
    ["flac", 'audio/mp4; codecs="flac"'],
    ["ac3", 'audio/mp4; codecs="ac-3"'],
    ["eac3", 'audio/mp4; codecs="ec-3"']
  ];
  var HLS_PROBES = [ // fMP4 through MSE (what hls.js uses)
    ["h264", 'video/mp4; codecs="avc1.640028"'],
    ["hevc", 'video/mp4; codecs="hvc1.2.4.L153.B0"'],
    ["av1", 'video/mp4; codecs="av01.0.08M.10"'],
    ["aac", 'audio/mp4; codecs="mp4a.40.2"'],
    ["mp3", 'audio/mp4; codecs="mp4a.69"'],
    ["opus", 'audio/mp4; codecs="opus"'],
    ["flac", 'audio/mp4; codecs="flac"'],
    ["ac3", 'audio/mp4; codecs="ac-3"'],
    ["eac3", 'audio/mp4; codecs="ec-3"']
  ];
  var VIDEO_TOKENS = ["h264", "hevc", "av1", "vp9"];

  function capabilities(videoEl) {
    var fallback = { video: ["h264"], audio: ["aac", "mp3"], hls: ["h264", "aac"] };
    try {
      var el = videoEl || document.createElement("video");
      var canPlay = function (type) {
        var r = el.canPlayType(type);
        return r === "probably" || r === "maybe";
      };
      var out = { video: [], audio: [], hls: [] };
      VIDEO_PROBES.forEach(function (p) { if (canPlay(p[1])) { out.video.push(p[0]); } });
      AUDIO_PROBES.forEach(function (p) { if (canPlay(p[1])) { out.audio.push(p[0]); } });
      if (out.audio.indexOf("mp3") < 0 && canPlay('audio/mp4; codecs="mp4a.69"')) { out.audio.push("mp3"); }
      var MS = global.MediaSource || global.ManagedMediaSource;
      if (MS && typeof MS.isTypeSupported === "function") {
        HLS_PROBES.forEach(function (p) { if (MS.isTypeSupported(p[1])) { out.hls.push(p[0]); } });
      } else if (canPlay("application/vnd.apple.mpegurl")) {
        // Native HLS (Safari/iOS without MSE): trust the plain-file results.
        out.video.forEach(function (c) { if (VIDEO_TOKENS.indexOf(c) >= 0 && c !== "vp9") { out.hls.push(c); } });
        out.audio.forEach(function (c) { out.hls.push(c); });
      }
      return out;
    } catch (e) {
      return fallback;
    }
  }

  function failFast(firstByteMs, loadMs) {
    return {
      default: {
        maxTimeToFirstByteMs: firstByteMs,
        maxLoadTimeMs: loadMs,
        timeoutRetry: { maxNumRetry: 2, retryDelayMs: 0, maxRetryDelayMs: 0 },
        errorRetry: { maxNumRetry: 2, retryDelayMs: 1000, maxRetryDelayMs: 4000 }
      }
    };
  }

  function createWeb(video) {
    var hls = null;
    var listeners = {};
    var domHandlers = [];
    var docHandlers = [];
    var pendingStart = 0;
    var trackEls = {};
    var shownId = null;       // id of the text track being shown
    var subDelay = 0;         // seconds added to every cue of the shown track (P16)
    var cueLevel = 0;         // 0 normal, 1 raised, 2 high (P15)
    var styleEl = null;
    var volumeProbe = null;   // can the page set the volume? (see canSetVolume)
    var CUE_LINES = [null, -3, -6];   // cue.line: negative counts lines up from the bottom
    var CUE_SIZES = { S: 90, M: 115, L: 150, XL: 200 };   // percent of the browser's default cue size

    function emit(name, detail) {
      (listeners[name] || []).slice().forEach(function (fn) {
        try { fn(detail); } catch (e) { console.error(e); }
      });
    }

    EVENTS.forEach(function (name) {
      var h = function () { emit(name); };
      video.addEventListener(name, h);
      domHandlers.push([name, h]);
    });
    var onErr = function () {
      var err = video.error;
      // 3 = decode error, 4 = format not supported: the file is the problem, not the network.
      var format = !!err && (err.code === 3 || err.code === 4);
      emit("error", { format: format, message: err && err.message ? err.message : "The video could not be played." });
    };
    video.addEventListener("error", onErr);
    domHandlers.push(["error", onErr]);

    var onMeta = function () {
      if (pendingStart > 0) {
        try { video.currentTime = pendingStart; } catch (e) { /* not seekable yet */ }
      }
      pendingStart = 0;
    };
    video.addEventListener("loadedmetadata", onMeta);
    domHandlers.push(["loadedmetadata", onMeta]);

    ["fullscreenchange", "webkitfullscreenchange"].forEach(function (name) {
      var h = function () { emit("fullscreenchange"); };
      document.addEventListener(name, h);
      docHandlers.push([name, h]);
    });
    // iPhone Safari uses the native fullscreen player for <video>.
    var onIosEnd = function () { emit("fullscreenchange"); };
    video.addEventListener("webkitendfullscreen", onIosEnd);
    domHandlers.push(["webkitendfullscreen", onIosEnd]);

    // Shift the cues of one track by the delay and put them on the chosen line. The original times
    // are kept on the cue (_nlStart/_nlEnd), so repeated changes never drift.
    function tune(track) {
      if (!track || !track.cues) { return; }
      var line = CUE_LINES[cueLevel];
      for (var i = 0; i < track.cues.length; i++) {
        var cue = track.cues[i];
        if (cue._nlStart === undefined) { cue._nlStart = cue.startTime; cue._nlEnd = cue.endTime; }
        cue.endTime = Math.max(0, cue._nlEnd + subDelay);
        cue.startTime = Math.max(0, cue._nlStart + subDelay);
        try {
          if (line === null) {
            if (cue._nlLined) { cue.line = "auto"; cue._nlLined = false; }
          } else {
            cue.snapToLines = true;
            cue.line = line;
            cue._nlLined = true;
          }
        } catch (e) { /* a browser that won't position cues */ }
      }
    }
    // Browsers may switch on a subtitle track by themselves when <track> elements are added (Chrome does after we
    // rebuild them for a new download). For a moment after we set the modes, put back what the page asked for.
    var enforceUntil = 0;
    function applyModes() {
      Object.keys(trackEls).forEach(function (key) {
        var el = trackEls[key];
        if (!el.track) { return; }
        var want = (shownId !== null && String(shownId) === key) ? "showing" : "disabled";
        if (el.track.mode !== want) { el.track.mode = want; }
      });
    }
    if (video.textTracks && video.textTracks.addEventListener) {
      video.textTracks.addEventListener("change", function () {
        if (Date.now() < enforceUntil) { applyModes(); }
      });
    }
    function shownTrack() {
      var el = shownId === null ? null : trackEls[String(shownId)];
      return el && el.track ? el.track : null;
    }

    function teardownHls() {
      if (hls) { hls.destroy(); hls = null; }
    }

    function clearTextTracks() {
      Object.keys(trackEls).forEach(function (id) {
        var el = trackEls[id];
        if (el.track) { el.track.mode = "disabled"; }
        if (el.parentNode) { el.parentNode.removeChild(el); }
      });
      trackEls = {};
    }

    return {
      load: function (src) {
        teardownHls();
        pendingStart = src.startAt > 0 ? src.startAt : 0;
        if (src.kind === "hls") {
          if (global.Hls && global.Hls.isSupported()) {
            hls = new global.Hls({
              // Give up quickly when Jellyfin's transcode fails (a 500 on every segment).
              manifestLoadPolicy: failFast(10000, 20000),
              playlistLoadPolicy: failFast(10000, 20000),
              fragLoadPolicy: failFast(20000, 60000)
            });
            hls.on(global.Hls.Events.ERROR, function (_evt, data) {
              if (data && data.fatal) {
                var code = data.response && data.response.code;
                var network = data.type === global.Hls.ErrorTypes.NETWORK_ERROR;
                var server = network && code >= 500 && code < 600;
                emit("error", {
                  format: data.type === global.Hls.ErrorTypes.MEDIA_ERROR,
                  server: !!server,
                  message: "Streaming error (" + (data.details || data.type) + ")."
                });
              }
            });
            hls.loadSource(src.url);
            hls.attachMedia(video);
          } else if (video.canPlayType("application/vnd.apple.mpegurl")) {
            video.src = src.url; // Safari and iOS play HLS natively
          } else {
            emit("error", { message: "This browser cannot play HLS video." });
          }
        } else {
          video.src = src.url;
        }
      },
      play: function () { return video.play(); },
      pause: function () { video.pause(); },
      seek: function (seconds) { video.currentTime = seconds; },
      get currentTime() { return video.currentTime; },
      get duration() { return video.duration; },
      get paused() { return video.paused; },
      get buffered() {
        var t = video.currentTime;
        for (var i = 0; i < video.buffered.length; i++) {
          if (video.buffered.start(i) <= t + 0.5 && video.buffered.end(i) >= t) {
            return video.buffered.end(i) - t;
          }
        }
        return 0;
      },
      get volume() { return video.volume; },
      set volume(v) { video.volume = Math.max(0, Math.min(1, v)); },
      get muted() { return video.muted; },
      set muted(m) { video.muted = !!m; },
      // Can the page set the volume at all? iPhones ignore video.volume (the hardware buttons own it), so the
      // swipe-for-volume gesture is only offered where a probe value sticks (M7a). Asked once.
      get canSetVolume() {
        if (volumeProbe === null) {
          try {
            var before = video.volume;
            var probe = before > 0.5 ? 0.4 : 0.6;
            video.volume = probe;
            volumeProbe = Math.abs(video.volume - probe) < 0.01;
            video.volume = before;
          } catch (e) { volumeProbe = false; }
        }
        return volumeProbe;
      },
      // Playback speed (M7a). defaultPlaybackRate keeps the choice when a new stream is loaded into the element.
      canChangeSpeed: true,
      get playbackRate() { return video.playbackRate; },
      set playbackRate(r) {
        r = Number(r);
        if (!(r >= 0.25 && r <= 4)) { return; }
        video.defaultPlaybackRate = r;
        video.playbackRate = r;
      },

      setTextTracks: function (list) {
        clearTextTracks();
        (list || []).forEach(function (t) {
          var el = document.createElement("track");
          el.kind = "subtitles";
          el.label = t.label || "";
          if (t.language) { el.srclang = t.language.slice(0, 3); }
          el.src = t.url;
          // The cues arrive after the track is first shown: shift/position them then.
          el.addEventListener("load", function () { if (shownId !== null && String(shownId) === String(t.id)) { tune(el.track); } });
          video.appendChild(el);
          if (el.track) { el.track.mode = "disabled"; }
          trackEls[String(t.id)] = el;
        });
        if (shownId !== null && !trackEls[String(shownId)]) { shownId = null; }
      },
      showTextTrack: function (id) {
        shownId = id === null || id === undefined ? null : id;
        enforceUntil = Date.now() + 1500;
        applyModes();
        tune(shownTrack());
      },
      canStyleSubtitles: true,
      canDelaySubtitles: true,
      get subtitleDelay() { return subDelay; },
      setSubtitleDelay: function (seconds) {
        subDelay = isFinite(seconds) ? seconds : 0;
        tune(shownTrack());
      },
      setSubtitleStyle: function (style) {
        style = style || {};
        cueLevel = Math.max(0, Math.min(2, Number(style.raise) || 0));
        var bg = style.background === "none" ? "background-color: transparent; text-shadow: none;"
          : style.background === "shadow" ? "background-color: transparent; text-shadow: 0 0 3px #000, 0 0 6px #000, 1px 1px 3px #000;"
          : "background-color: rgba(0, 0, 0, 0.72); text-shadow: none;";
        var css = "video::cue { color: #fff; font-size: " + (CUE_SIZES[style.size] || CUE_SIZES.M) + "%; " + bg + " }";
        if (!styleEl) {
          styleEl = document.createElement("style");
          styleEl.setAttribute("data-nl-cue-style", "");
          document.head.appendChild(styleEl);
        }
        styleEl.textContent = css;
        tune(shownTrack());
      },

      get isFullscreen() {
        return !!(document.fullscreenElement || document.webkitFullscreenElement || video.webkitDisplayingFullscreen);
      },
      toggleFullscreen: function (container) {
        var fsEl = document.fullscreenElement || document.webkitFullscreenElement;
        if (fsEl) {
          (document.exitFullscreen || document.webkitExitFullscreen).call(document);
        } else if (video.webkitDisplayingFullscreen) {
          video.webkitExitFullscreen();
        } else if (container && container.requestFullscreen) {
          container.requestFullscreen().catch(function () { /* refused */ });
        } else if (container && container.webkitRequestFullscreen) {
          container.webkitRequestFullscreen();
        } else if (video.webkitEnterFullscreen) {
          video.webkitEnterFullscreen();
        }
      },

      on: function (name, fn) {
        (listeners[name] = listeners[name] || []).push(fn);
      },
      destroy: function () {
        teardownHls();
        clearTextTracks();
        domHandlers.forEach(function (p) { video.removeEventListener(p[0], p[1]); });
        docHandlers.forEach(function (p) { document.removeEventListener(p[0], p[1]); });
        domHandlers = [];
        docHandlers = [];
        listeners = {};
        if (styleEl && styleEl.parentNode) { styleEl.parentNode.removeChild(styleEl); }
        video.removeAttribute("src");
        video.load();
      }
    };
  }

  /* ------------------------------------------------------------- native adapter (bridge v2)
   * Inside the Android TV app the page does not decode anything: window.NorthernLightsNative
   * (Android addJavascriptInterface; every method takes and returns strings/numbers) plays through
   * Media3 behind the transparent WebView and reports back through window.NLNativeEvents.dispatch(jsonString).
   * The page draws every control; native only decodes, draws the picture and text subtitles.
   *
   *   NorthernLightsNative.play(json)      json = {url, kind, startSeconds, title, subtitle, skipSeconds,
   *       audioTracks:[{index,label,language,isDefault}], subtitleTracks:[{index,label,language,url,kind}],
   *       audioIndex, subtitleIndex, nextUp: null | {label}}. url and subtitle urls are absolute
   *       (our /nl/jf/... proxy, never Jellyfin). Calling play again replaces the current stream
   *       (audio change, or a picture subtitle: the page re-fetches and plays again at startSeconds).
   *   NorthernLightsNative.pause() / resume()
   *   NorthernLightsNative.seekTo(seconds)  seeks inside the current stream (no restart)
   *   NorthernLightsNative.setSubtitle(i)   show the side-loaded text track with the page's index i, -1 = off
   *   NorthernLightsNative.setSubtitleStyle(json)   (version 3) json = {size: S|M|L|XL, background: none|shadow|box,
   *                                         raise: 0|1|2}; styles the subtitles Media3 draws (works before and during playback)
   *   NorthernLightsNative.setSpeed(rate)   (version 4) playback speed as a string, "0.75" ... "2" (Media3 setPlaybackSpeed);
   *                                         the page re-sends it after every "started" because a new stream starts at 1x
   *   NorthernLightsNative.setAudioTrack(n) (version 5) select the n-th audio track of the file (0-based, file order = the server's
   *                                         audio_order) inside the playing stream, no reload. Only for kind "file" (the original); the
   *                                         play() json carries audioOrder for the first selection.
   *   NorthernLightsNative.stop()           leave the native player (page teardown)
   *   NorthernLightsNative.version()        "5" (v5 = v4 + setAudioTrack, audioOrder in play()), "4" (v4 = v3 + setSpeed), "3" (v3 = v2 + setSubtitleStyle; there is no subtitle delay on the TV), "2" (v2 above); "1" = old app without pause/resume/seekTo/
   *                                         setSubtitle and with its own native controls: the web
   *                                         controls then stay hidden and seek() restarts the stream
   *   NLNativeEvents.dispatch(json)         json = {type: started|time|progress|paused|resumed|seeked|
   *       stopped|ended|error, positionSeconds, durationSeconds, bufferedSeconds (seconds buffered ahead of
   *       the playhead), playing (bool), message?}. "time" arrives every 500 ms while a player exists,
   *       "progress" every 10 s while playing.
   *
   * The adapter has the same surface as the web one: play/pause/seek call the bridge, currentTime /
   * duration / paused / buffered come from the events. Extra events: "progress" (every 10 s while
   * playing) and "stopped" (the native player was closed from outside, e.g. the app went to the
   * background). load() reads the extra field src.native = {title, subtitle, skipSeconds, audioTracks,
   * subtitleTracks, audioIndex, subtitleIndex, nextUp}. */

  function absolute(path) {
    if (!path) { return null; }
    try { return new URL(path, global.location.origin).href; } catch (e) { return path; }
  }

  function isNative() {
    return !!global.NorthernLightsNative && typeof global.NorthernLightsNative.play === "function";
  }

  function nativeVersion() {
    try { return String(global.NorthernLightsNative.version()); } catch (e) { return ""; }
  }

  function createNative() {
    var v2 = Number(nativeVersion()) >= 2;
    var v3 = Number(nativeVersion()) >= 3;
    var v4 = Number(nativeVersion()) >= 4;
    var v5 = Number(nativeVersion()) >= 5;
    var rate = 1;
    var listeners = {};
    var pos = 0;
    var dur = 0;
    var buf = 0;
    var paused = true;
    var waiting = false;
    var closed = true;       // no native player right now (never started, ended, errored or stopped)
    var ready = false;       // native reported "started" for the current stream
    var lastSrc = null;
    var alive = true;
    var tick = null;
    var tickAt = 0;

    function emit(name, detail) {
      (listeners[name] || []).slice().forEach(function (fn) {
        try { fn(detail); } catch (e) { console.error(e); }
      });
    }

    // v1 only (the old app has no "time" events): between its 10 s "progress" events the position is estimated.
    function startTick() {
      stopTick();
      if (v2) { return; }
      tickAt = Date.now();
      tick = setInterval(function () {
        var now = Date.now();
        if (!paused) {
          pos += (now - tickAt) / 1000;
          if (dur > 0 && pos > dur) { pos = dur; }
          emit("timeupdate");
        }
        tickAt = now;
      }, 500);
    }
    function stopTick() { if (tick) { clearInterval(tick); tick = null; } }

    function sync(ev) {
      if (typeof ev.positionSeconds === "number" && isFinite(ev.positionSeconds)) { pos = ev.positionSeconds; }
      if (typeof ev.durationSeconds === "number" && isFinite(ev.durationSeconds) && ev.durationSeconds > 0) { dur = ev.durationSeconds; }
      if (typeof ev.bufferedSeconds === "number" && isFinite(ev.bufferedSeconds)) { buf = Math.max(0, ev.bufferedSeconds); }
      tickAt = Date.now();
    }

    function setPaused(value) {
      if (paused === value) { return; }
      paused = value;
      if (value) {
        emit("pause");
      } else {
        emit("play");
        emit("playing");
      }
    }

    function handle(ev) {
      if (!alive || !ev || typeof ev.type !== "string") { return; }
      sync(ev);
      switch (ev.type) {
        case "started":
          closed = false;
          ready = true;
          waiting = false;
          paused = false;
          if (v4 && rate !== 1) { call("setSpeed", String(rate)); }   // a new native stream starts at 1x
          startTick();
          emit("loadedmetadata");
          emit("play");
          emit("playing");
          emit("timeupdate");
          break;
        case "time":
          if (!paused && ev.playing === false && !waiting) {
            waiting = true;       // stalled: the page shows its spinner
            emit("waiting");
          } else if (waiting && ev.playing === true) {
            waiting = false;
            emit("playing");
          }
          emit("timeupdate");
          break;
        case "progress":
          emit("timeupdate");
          emit("progress");
          break;
        case "paused":
          setPaused(true);
          break;
        case "resumed":
          setPaused(false);
          break;
        case "seeked":
          emit("seeked");
          emit("timeupdate");
          break;
        case "stopped":
          paused = true;
          closed = true;
          stopTick();
          emit("stopped");
          break;
        case "ended":
          closed = true;
          stopTick();
          setPaused(true);
          emit("ended");
          break;
        case "error":
          paused = true;
          closed = true;
          stopTick();
          emit("error", { format: false, message: ev.message || "The video could not be played." });
          break;
        case "audio":
          emit("audiochange", { index: ev.index });
          break;
        case "subtitle":
          emit("subtitlechange", { index: ev.index });
          break;
        default:
          break;
      }
    }

    global.NLNativeEvents = {
      dispatch: function (json) {
        var ev = null;
        try { ev = typeof json === "string" ? JSON.parse(json) : json; } catch (e) { return; }
        handle(ev);
      }
    };

    function call(name, arg) {
      try {
        var api = global.NorthernLightsNative;
        if (arg === undefined) { api[name](); } else { api[name](arg); }
      } catch (e) { /* the app is gone or too old for this call */ }
    }

    function send(src, startAt) {
      var n = src.native || {};
      var payload = {
        url: absolute(src.url),
        kind: src.kind === "hls" ? "hls" : "file",
        startSeconds: startAt > 0 ? startAt : 0,
        title: n.title || "",
        subtitle: n.subtitle || "",
        skipSeconds: n.skipSeconds || 10,
        audioTracks: (n.audioTracks || []).map(function (t) {
          return { index: t.index, label: t.label || "", language: t.language || "", isDefault: !!t.is_default };
        }),
        subtitleTracks: (n.subtitleTracks || []).map(function (t) {
          return { index: t.index, label: t.label || "", language: t.language || "", url: absolute(t.url), kind: t.kind || "text" };
        }),
        audioIndex: n.audioIndex === undefined ? null : n.audioIndex,
        audioOrder: v5 && typeof n.audioOrder === "number" ? n.audioOrder : null,
        subtitleIndex: n.subtitleIndex === undefined ? -1 : n.subtitleIndex,
        nextUp: n.nextUp || null
      };
      pos = payload.startSeconds;
      buf = 0;
      paused = true;
      waiting = false;
      closed = false;
      ready = false;
      try {
        global.NorthernLightsNative.play(JSON.stringify(payload));
      } catch (e) {
        closed = true;
        emit("error", { format: false, message: "The TV player could not start." });
      }
    }

    return {
      native: true,
      nativeVersion: v5 ? 5 : v4 ? 4 : (v3 ? 3 : (v2 ? 2 : 1)),
      load: function (src) {
        lastSrc = src;
        dur = 0;
        send(src, src.startAt);
      },
      play: function () {
        if (!v2) { return Promise.resolve(); }
        if (closed) {                       // the native player ended or failed: start the stream again
          if (lastSrc) { send(lastSrc, dur > 0 && pos >= dur - 0.5 ? 0 : pos); }
          return Promise.resolve();
        }
        if (!ready) { return Promise.resolve(); }   // the stream starts by itself
        call("resume");
        setPaused(false);                   // optimistic; the "resumed" event confirms
        return Promise.resolve();
      },
      pause: function () {
        if (!v2) { return; }
        call("pause");
        setPaused(true);
      },
      seek: function (seconds) {
        if (!lastSrc) { return; }
        var target = Math.max(0, seconds);
        if (!v2 || closed) {
          send(lastSrc, target);            // old app, or nothing to seek in: (re)start at that position
          return;
        }
        pos = target;
        call("seekTo", String(target));
        emit("seeking");
        emit("timeupdate");
      },
      get currentTime() { return pos; },
      get duration() { return dur || NaN; },
      get paused() { return paused; },
      get buffered() { return buf; },
      get volume() { return 1; },
      set volume(v) { /* the TV's own volume */ },
      get muted() { return false; },
      set muted(m) { /* the TV's own volume */ },
      canSetVolume: false,
      // Speed needs an app with bridge version 4; older apps keep playing at 1x and the page hides the speed menu.
      canChangeSpeed: v4,
      get playbackRate() { return rate; },
      set playbackRate(r) {
        r = Number(r);
        if (!v4 || !(r >= 0.25 && r <= 4)) { return; }
        rate = r;
        if (ready && !closed) { call("setSpeed", String(rate)); }
      },
      // Bridge v5: switch audio inside the playing original file (no new stream). Older apps: the page reloads instead.
      canSwitchAudioInPlace: v5,
      setAudioOrder: function (order) {
        if (v5 && !closed && typeof order === "number" && order >= 0) { call("setAudioTrack", String(order)); }
      },
      setTextTracks: function () { /* the native player loads them from the play() json */ },
      showTextTrack: function (id) {
        if (v2) { call("setSubtitle", id === null || id === undefined ? -1 : Number(id)); }
      },
      // Media3 1.5.1 has no subtitle offset, so the TV offers no delay; style needs an app with bridge version 3.
      canDelaySubtitles: false,
      canStyleSubtitles: v3,
      get subtitleDelay() { return 0; },
      setSubtitleDelay: function () { /* unsupported on the TV */ },
      setSubtitleStyle: function (style) {
        if (v3) { call("setSubtitleStyle", JSON.stringify(style || {})); }
      },
      get isFullscreen() { return true; },
      toggleFullscreen: function () { /* always full screen */ },
      on: function (name, fn) { (listeners[name] = listeners[name] || []).push(fn); },
      destroy: function () {
        alive = false;
        stopTick();
        listeners = {};
        try { global.NorthernLightsNative.stop(); } catch (e) { /* already gone */ }
      }
    };
  }

  function create(video) {
    return isNative() ? createNative() : createWeb(video);
  }

  global.NLPlayerCore = { create: create, capabilities: capabilities, isNative: isNative, nativeVersion: nativeVersion };
})(window);
