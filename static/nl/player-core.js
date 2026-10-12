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
 *   ("2": pause/resume/seekTo/setSubtitle + "time" events, the web controls work; "1": old app).
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

      setTextTracks: function (list) {
        clearTextTracks();
        (list || []).forEach(function (t) {
          var el = document.createElement("track");
          el.kind = "subtitles";
          el.label = t.label || "";
          if (t.language) { el.srclang = t.language.slice(0, 3); }
          el.src = t.url;
          video.appendChild(el);
          if (el.track) { el.track.mode = "disabled"; }
          trackEls[String(t.id)] = el;
        });
      },
      showTextTrack: function (id) {
        Object.keys(trackEls).forEach(function (key) {
          var el = trackEls[key];
          if (el.track) { el.track.mode = (id !== null && String(id) === key) ? "showing" : "disabled"; }
        });
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
   *   NorthernLightsNative.stop()           leave the native player (page teardown)
   *   NorthernLightsNative.version()        "2" (v2 above); "1" = old app without pause/resume/seekTo/
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
      nativeVersion: v2 ? 2 : 1,
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
      setTextTracks: function () { /* the native player loads them from the play() json */ },
      showTextTrack: function (id) {
        if (v2) { call("setSubtitle", id === null || id === undefined ? -1 : Number(id)); }
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
