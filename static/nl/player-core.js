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
 */
(function (global) {
  "use strict";

  var EVENTS = ["play", "pause", "timeupdate", "ended", "waiting", "playing", "loadedmetadata",
    "seeking", "seeked", "volumechange"];

  function create(video) {
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
            hls = new global.Hls();
            hls.on(global.Hls.Events.ERROR, function (_evt, data) {
              if (data && data.fatal) {
                emit("error", {
                  format: data.type === global.Hls.ErrorTypes.MEDIA_ERROR,
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

  global.NLPlayerCore = { create: create };
})(window);
