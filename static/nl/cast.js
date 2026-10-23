/* Northern Lights casting, sender side (M8a).
 *
 * The only file that touches Google's Cast Web Sender SDK (cast_sender.js, loaded on demand from gstatic.com).
 * The player UI keeps talking to ONE adapter surface (see player-core.js); while a Chromecast is connected the
 * adapter underneath is swapped from the <video> to the Chromecast, so play/pause, skip, seek, volume, audio and
 * subtitle tracks, next episode and progress reporting all keep working with the phone as the remote.
 *
 *   var kit = NLCast.attach(window, localAdapter, enabled);
 *   kit.player                   // the adapter the page uses: the local one, or the Chromecast while casting
 *   kit.cast.init(config)        // config from /api/nl/cast/config; loads the SDK when this browser can cast
 *   kit.cast.on("state"|"connected"|"ended", fn)
 *   kit.cast.request()           // opens the device picker      kit.cast.end(stopTheReceiver)
 *   kit.activate() / kit.deactivate() / kit.active()
 *
 * Casting needs a secure context (HTTPS, or localhost) and a Chromium-based browser. Nothing is loaded and the
 * Cast button never shows anywhere else. The stream the Chromecast gets (an absolute LAN URL with a signed,
 * 6-hour token) comes from /api/nl/cast/play/{id}; this file never builds URLs and never sees a Jellyfin key.
 */
(function (global) {
  "use strict";

  var SDK_URL = "https://www.gstatic.com/cv/js/sender/v1/cast_sender.js?loadCastFramework=1";
  var SDK_TIMEOUT_MS = 10000;
  var SUB_TRACK_BASE = 1000;     // Cast track ids for subtitles: 1000 + the page's subtitle index

  // Everything player.js uses on `player` must be listed here, or the page never sees it (it is the switch, not the
  // adapter). The last methods and props are the TV app's (bridge v5/v6); the Chromecast adapter has none of them.
  var METHODS = ["load", "play", "pause", "seek", "setTextTracks", "showTextTrack", "setSubtitleDelay",
    "setSubtitleStyle", "toggleFullscreen", "destroy", "setAudioOrder", "stats"];
  var PROPS = ["currentTime", "duration", "paused", "buffered", "volume", "muted", "canSetVolume", "canChangeSpeed",
    "playbackRate", "canStyleSubtitles", "canDelaySubtitles", "subtitleDelay", "isFullscreen",
    "canSwitchAudioInPlace", "canDrawEmbeddedSubtitles", "canShowStats"];

  /* ------------------------------------------------------------------ feature detection and SDK loading */

  /** Secure context (the SDK refuses to work otherwise) and a Chromium browser (it exists nowhere else). */
  function eligible(win) {
    return !!(win && win.isSecureContext && win.chrome && !win.NorthernLightsNative);
  }

  function sdkReady(win) {
    return !!(win.cast && win.cast.framework && win.chrome && win.chrome.cast && win.chrome.cast.media);
  }

  function loadSdk(win, done) {
    if (sdkReady(win)) { done(true); return; }
    var finished = false;
    function finish(ok) { if (!finished) { finished = true; done(!!ok); } }
    win.__onGCastApiAvailable = function (isAvailable) { finish(isAvailable && sdkReady(win)); };
    var script = win.document.createElement("script");
    script.src = SDK_URL;
    script.async = true;
    script.onerror = function () { finish(false); };
    win.document.head.appendChild(script);
    win.setTimeout(function () { finish(false); }, SDK_TIMEOUT_MS);
  }

  /* ------------------------------------------------------------------ the switch (local <-> Chromecast) */

  function createSwitch(local, remote) {
    var active = local;
    var relayed = {};
    var handlers = {};
    var player = {};
    METHODS.forEach(function (name) {
      player[name] = function () { return typeof active[name] === "function" ? active[name].apply(active, arguments) : undefined; };
    });
    PROPS.forEach(function (name) {
      Object.defineProperty(player, name, {
        enumerable: true,
        get: function () { return active[name]; },
        set: function (value) { active[name] = value; }
      });
    });
    // An event reaches the page only from the adapter that is in charge right now.
    player.on = function (name, fn) {
      (handlers[name] = handlers[name] || []).push(fn);
      if (relayed[name]) { return; }
      relayed[name] = true;
      [local, remote].forEach(function (source) {
        source.on(name, function (detail) {
          if (active !== source) { return; }
          (handlers[name] || []).slice().forEach(function (h) { h(detail); });
        });
      });
    };
    return {
      player: player,
      local: local,
      remote: remote,
      active: function () { return active === remote; },
      activate: function () { active = remote; },
      deactivate: function () { active = local; }
    };
  }

  /* ------------------------------------------------------------------ the Chromecast as a player adapter */

  function createRemote() {
    var b = null;                       // { context, player, controller, win } once the SDK is ready
    var listeners = {};
    var st = {
      pos: 0, dur: NaN, paused: true, volume: 1, muted: false,
      loaded: false, ended: false, seeking: false, loadId: 0, wantText: null, appliedText: null, playedOnce: false
    };

    function emit(name, detail) {
      (listeners[name] || []).slice().forEach(function (fn) { fn(detail); });
    }

    function ns() { return b.win.chrome.cast; }
    function session() { return b && b.context.getCurrentSession(); }

    function bind(backend) {
      b = backend;
      var T = b.win.cast.framework.RemotePlayerEventType;
      b.controller.addEventListener(T.ANY_CHANGE, onChange);
    }

    function onChange(ev) {
      if (!b || !ev) { return; }
      switch (ev.field) {
        case "currentTime":
          if (st.loaded) { st.pos = Number(ev.value) || 0; emit("timeupdate"); }
          break;
        case "duration":
          if (ev.value > 0) { st.dur = Number(ev.value); emit("loadedmetadata"); emit("timeupdate"); }
          break;
        case "isPaused":
          st.paused = !!ev.value;
          emit(st.paused ? "pause" : "play");
          break;
        case "volumeLevel":
          st.volume = Number(ev.value);
          emit("volumechange");
          break;
        case "isMuted":
          st.muted = !!ev.value;
          emit("volumechange");
          break;
        case "playerState":
          onState(ev.value);
          break;
        default:
          break;
      }
    }

    function idleReason() {
      var s = session();
      var media = s && s.getMediaSession && s.getMediaSession();
      return media ? media.idleReason : null;
    }

    function onState(value) {
      if (value === "PLAYING") {
        st.playedOnce = true;
        st.paused = false;
        emit("playing");
        if (st.seeking) { st.seeking = false; emit("seeked"); }
      } else if (value === "BUFFERING") {
        emit("waiting");
      } else if (value === "PAUSED") {
        if (st.seeking) { st.seeking = false; emit("seeked"); }
      } else if (value === "IDLE" && st.loaded && st.playedOnce && !st.ended && idleReason() === "FINISHED") {
        st.ended = true;
        emit("ended");
      }
    }

    function applyText() {
      if (!b || !st.loaded) { return; }
      var ids = st.wantText === null ? [] : [SUB_TRACK_BASE + st.wantText];
      var key = ids.join(",");
      if (key === st.appliedText) { return; }
      var s = session();
      var media = s && s.getMediaSession && s.getMediaSession();
      if (!media) { return; }
      st.appliedText = key;
      media.editTracksInfo(new (ns().media.EditTracksInfoRequest)(ids), function () {}, function () {});
    }

    function buildLoadRequest(stream, startAt) {
      var c = ns();
      var info = new c.media.MediaInfo(stream.url, stream.cast.content_type);
      info.streamType = c.media.StreamType.BUFFERED;
      if (stream.duration_seconds) { info.duration = stream.duration_seconds; }
      var meta = new c.media.GenericMediaMetadata();
      meta.title = stream.title || "";
      var ep = stream.episode;
      var epText = "";
      if (ep && (ep.season || ep.episode || ep.name)) {
        var bits = [];
        if (ep.season != null) { bits.push("S" + ep.season); }
        if (ep.episode != null) { bits.push("E" + ep.episode); }
        if (ep.name) { bits.push(ep.name); }
        epText = bits.join(" · ");
        meta.subtitle = epText;
      }
      if (stream.cast.backdrop) { meta.images = [new c.Image(stream.cast.backdrop)]; }
      info.metadata = meta;
      // Everything the custom receiver draws comes from here: it has no cookies and calls nothing of ours.
      info.customData = {
        nl: 1,
        item_id: stream.media_item_id,
        source_id: stream.source_id,
        title: stream.title || "",
        episode: epText,
        backdrop: stream.cast.backdrop || "",
        poster: stream.cast.poster || ""
      };
      var tracks = [];
      (stream.subtitle_tracks || []).forEach(function (t) {
        if (t.kind !== "text" || !t.url) { return; }
        var track = new c.media.Track(SUB_TRACK_BASE + t.index, c.media.TrackType.TEXT);
        track.trackContentId = t.url;
        track.trackContentType = "text/vtt";
        track.subtype = c.media.TextTrackType.SUBTITLES;
        track.name = t.label || "";
        if (t.language) { track.language = t.language; }
        tracks.push(track);
      });
      if (tracks.length) { info.tracks = tracks; }
      var req = new c.media.LoadRequest(info);
      req.currentTime = startAt > 0 ? startAt : 0;
      req.autoplay = true;
      req.activeTrackIds = st.wantText === null ? [] : [SUB_TRACK_BASE + st.wantText];
      st.appliedText = req.activeTrackIds.join(",");
      return req;
    }

    return {
      bind: bind,
      lastState: st,
      load: function (src) {
        var stream = src && src.cast;
        var s = session();
        if (!b || !stream || !s) { emit("error", { format: false, message: "The TV isn't connected." }); return; }
        st.pos = src.startAt > 0 ? src.startAt : 0;
        st.dur = stream.duration_seconds > 0 ? stream.duration_seconds : NaN;
        st.paused = false;
        st.loaded = false;
        st.ended = false;
        st.playedOnce = false;
        st.seeking = false;
        st.loadId += 1;
        var mine = st.loadId;
        var request = buildLoadRequest(stream, st.pos);
        s.loadMedia(request).then(function (errorCode) {
          if (mine !== st.loadId) { return; }
          if (errorCode) { emit("error", { format: false, message: "The TV couldn't load this (" + errorCode + ")." }); return; }
          st.loaded = true;
          applyText();
        }, function (err) {
          if (mine !== st.loadId) { return; }
          emit("error", { format: false, message: "The TV couldn't load this" + (err ? " (" + err + ")." : ".") });
        });
      },
      // While a load is in flight the TV starts by itself (autoplay), so a press then has nothing to toggle.
      play: function () {
        if (b && st.loaded && b.player.isPaused) { b.controller.playOrPause(); }
        return Promise.resolve();
      },
      pause: function () { if (b && st.loaded && !b.player.isPaused) { b.controller.playOrPause(); } },
      seek: function (seconds) {
        st.pos = Math.max(0, seconds);
        if (!b || !st.loaded) { return; }
        st.seeking = true;
        b.player.currentTime = st.pos;
        b.controller.seek();
        emit("seeking");
        emit("timeupdate");
      },
      get currentTime() { return st.pos; },
      get duration() { return st.dur; },
      get paused() { return st.paused; },
      get buffered() { return 0; },
      get volume() { return st.volume; },
      set volume(v) {
        st.volume = Math.max(0, Math.min(1, Number(v)));
        if (b) { b.player.volumeLevel = st.volume; b.controller.setVolumeLevel(); }
      },
      get muted() { return st.muted; },
      set muted(m) {
        st.muted = !!m;
        if (b && !!b.player.isMuted !== st.muted) { b.controller.muteOrUnmute(); }
      },
      canSetVolume: true,
      canChangeSpeed: false,       // a Chromecast plays at 1x
      get playbackRate() { return 1; },
      set playbackRate(r) { /* not supported while casting */ },
      setTextTracks: function () { /* the tracks travel inside the load request */ },
      showTextTrack: function (id) {
        st.wantText = id === null || id === undefined ? null : Number(id);
        applyText();
      },
      canStyleSubtitles: false,    // the receiver draws them
      canDelaySubtitles: false,
      get subtitleDelay() { return 0; },
      setSubtitleDelay: function () {},
      setSubtitleStyle: function () {},
      get isFullscreen() { return false; },
      toggleFullscreen: function () {},
      on: function (name, fn) { (listeners[name] = listeners[name] || []).push(fn); },
      destroy: function () {},
      /** Called by the Cast layer when the session ends: freeze the position, drop the load in flight. */
      detach: function () { st.loadId += 1; st.loaded = false; },
      emit: emit
    };
  }

  /* ------------------------------------------------------------------ the Cast context */

  function createCast(win, remote) {
    var listeners = {};
    var api = {
      state: "unavailable",     // unavailable | no-devices | ready | connecting | connected
      deviceName: "",
      config: null,
      on: function (name, fn) { (listeners[name] = listeners[name] || []).push(fn); },
      init: init,
      request: request,
      end: end
    };
    var backend = null;

    function emit(name, detail) { (listeners[name] || []).slice().forEach(function (fn) { fn(detail); }); }
    function setState(next) {
      if (next === api.state) { return; }
      api.state = next;
      emit("state", next);
    }

    function init(config) {
      api.config = config || {};
      if (!api.config.enabled || !eligible(win)) { return; }
      loadSdk(win, function (ok) {
        if (!ok) { return; }
        var cast = win.cast;
        var context = cast.framework.CastContext.getInstance();
        context.setOptions({
          receiverApplicationId: api.config.app_id || win.chrome.cast.media.DEFAULT_MEDIA_RECEIVER_APP_ID,
          autoJoinPolicy: win.chrome.cast.AutoJoinPolicy.ORIGIN_SCOPED
        });
        var player = new cast.framework.RemotePlayer();
        var controller = new cast.framework.RemotePlayerController(player);
        backend = { context: context, player: player, controller: controller, win: win };
        remote.bind(backend);
        var E = cast.framework.CastContextEventType;
        context.addEventListener(E.CAST_STATE_CHANGED, function (e) { applyCastState(e.castState); });
        context.addEventListener(E.SESSION_STATE_CHANGED, function (e) { onSession(e); });
        applyCastState(context.getCastState());
        // A session this tab joined earlier may already be running (the page was reloaded while casting).
        var S = cast.framework.SessionState;
        if (context.getCurrentSession() && context.getCastState() === cast.framework.CastState.CONNECTED) {
          onSession({ sessionState: S.SESSION_RESUMED });
        }
      });
    }

    function applyCastState(value) {
      var C = win.cast.framework.CastState;
      if (value === C.NO_DEVICES_AVAILABLE) { setState("no-devices"); }
      else if (value === C.NOT_CONNECTED) { setState("ready"); }
      else if (value === C.CONNECTING) { setState("connecting"); }
      else if (value === C.CONNECTED) { setState("connected"); }
    }

    function onSession(e) {
      var S = win.cast.framework.SessionState;
      var value = e && e.sessionState;
      if (value === S.SESSION_STARTED || value === S.SESSION_RESUMED) {
        var s = backend.context.getCurrentSession();
        var device = s && s.getCastDevice && s.getCastDevice();
        api.deviceName = (device && device.friendlyName) || "your TV";
        var info = backend.player.mediaInfo;
        emit("connected", {
          resumed: value === S.SESSION_RESUMED,
          deviceName: api.deviceName,
          item: (info && info.customData && info.customData.nl) ? info.customData : null,
          position: Number(backend.player.currentTime) || 0
        });
      } else if (value === S.SESSION_ENDED) {
        remote.detach();
        emit("ended", { position: remote.lastState.pos });
      }
    }

    function request() {
      if (!backend) { return Promise.resolve(); }
      return backend.context.requestSession().then(function () {}, function () { /* picker closed */ });
    }

    function end(stopReceiver) {
      if (backend) { backend.context.endCurrentSession(!!stopReceiver); }
    }

    return api;
  }

  /** Wires the pieces together for one page. `enabled` false (the TV app, ?tv=1): nothing is ever loaded. */
  function attach(win, local, enabled) {
    var remote = createRemote();
    var kit = createSwitch(local, remote);
    kit.cast = createCast(win, remote);
    kit.enabled = !!enabled;
    var init = kit.cast.init;
    kit.cast.init = function (config) { if (kit.enabled) { init(config); } };
    return kit;
  }

  global.NLCast = {
    attach: attach,
    createSwitch: createSwitch,
    createRemote: createRemote,
    createCast: createCast,
    eligible: eligible,
    SDK_URL: SDK_URL,
    SUB_TRACK_BASE: SUB_TRACK_BASE
  };
})(window);
