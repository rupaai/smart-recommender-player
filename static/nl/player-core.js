/* Northern Lights player adapter (S3).
 * The rest of the UI talks only to this interface; nothing else may touch hls.js.
 *
 *   const p = NLPlayerCore.create(videoElement);
 *   p.load({url, kind});   // kind: "hls" | "file"
 *   p.play(); p.pause(); p.seek(seconds);
 *   p.currentTime; p.duration;
 *   p.on("play"|"pause"|"timeupdate"|"ended"|"waiting"|"playing"|"loadedmetadata"|"error", fn);
 *   p.destroy();
 */
(function (global) {
  "use strict";

  var EVENTS = ["play", "pause", "timeupdate", "ended", "waiting", "playing", "loadedmetadata"];

  function create(video) {
    var hls = null;
    var listeners = {};
    var domHandlers = [];

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
      emit("error", { message: err && err.message ? err.message : "The video could not be played." });
    };
    video.addEventListener("error", onErr);
    domHandlers.push(["error", onErr]);

    function teardownHls() {
      if (hls) { hls.destroy(); hls = null; }
    }

    return {
      load: function (src) {
        teardownHls();
        if (src.kind === "hls") {
          if (global.Hls && global.Hls.isSupported()) {
            hls = new global.Hls();
            hls.on(global.Hls.Events.ERROR, function (_evt, data) {
              if (data && data.fatal) {
                emit("error", { message: "Streaming error (" + (data.details || data.type) + ")." });
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
      on: function (name, fn) {
        (listeners[name] = listeners[name] || []).push(fn);
      },
      destroy: function () {
        teardownHls();
        domHandlers.forEach(function (p) { video.removeEventListener(p[0], p[1]); });
        domHandlers = [];
        listeners = {};
        video.removeAttribute("src");
        video.load();
      }
    };
  }

  global.NLPlayerCore = { create: create };
})(window);
