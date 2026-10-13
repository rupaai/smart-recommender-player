/* Northern Lights per-person prefs (M4, U3). Subtitle look, subtitle search language, preferred audio
 * and subtitle language and "subtitles on by default" belong to the person who is watching, not to the
 * device: they live on the server (GET/PUT /api/nl/prefs, the viewer comes from the nl_viewer cookie).
 *
 *   NLPrefs.get()          Promise of the prefs {audio_lang, subtitle_lang, subtitles_on, sub_style, search_lang}
 *   NLPrefs.peek()         the prefs once loaded, else a localStorage-only guess (never throws)
 *   NLPrefs.save(patch)    Promise; updates the cache at once and the server in the background
 *   NLPrefs.languages      [[code, name], ...] for pickers
 *
 * localStorage is the fallback and the migration source: M5 saved the look in nl.subStyle and the
 * search language in nl.subLang per device. The first time a person loads their prefs and has none of
 * those saved, the device's values are copied into their prefs. If the server cannot be reached, a
 * saved change is kept in localStorage instead. */
(function (global) {
  "use strict";

  var STYLE_KEY = "nl.subStyle";
  var LANG_KEY = "nl.subLang";
  var SIZES = ["S", "M", "L", "XL"];
  var BACKGROUNDS = ["none", "shadow", "box"];
  var LANGUAGES = [
    ["eng", "English"], ["spa", "Spanish"], ["fra", "French"], ["deu", "German"], ["ita", "Italian"],
    ["por", "Portuguese"], ["nld", "Dutch"], ["swe", "Swedish"], ["nor", "Norwegian"], ["dan", "Danish"],
    ["fin", "Finnish"], ["pol", "Polish"], ["rus", "Russian"], ["ukr", "Ukrainian"], ["tur", "Turkish"],
    ["ell", "Greek"], ["ces", "Czech"], ["hun", "Hungarian"], ["ron", "Romanian"], ["heb", "Hebrew"],
    ["ara", "Arabic"], ["fas", "Persian"], ["hin", "Hindi"], ["ben", "Bengali"], ["urd", "Urdu"],
    ["tam", "Tamil"], ["zho", "Chinese"], ["jpn", "Japanese"], ["kor", "Korean"], ["tha", "Thai"],
    ["vie", "Vietnamese"], ["ind", "Indonesian"]
  ];

  var cache = null;
  var loading = null;

  function lsGet(k) { try { return global.localStorage.getItem(k); } catch (e) { return null; } }
  function lsSet(k, v) { try { global.localStorage.setItem(k, String(v)); } catch (e) { /* private mode */ } }

  function validStyle(s) {
    return !!s && typeof s === "object" && SIZES.indexOf(s.size) >= 0 && BACKGROUNDS.indexOf(s.background) >= 0 &&
      (s.raise === 0 || s.raise === 1 || s.raise === 2);
  }
  function localStyle() {
    try {
      var s = JSON.parse(lsGet(STYLE_KEY) || "null");
      return validStyle(s) ? { size: s.size, background: s.background, raise: s.raise } : null;
    } catch (e) { return null; }
  }
  function localLang() {
    var v = lsGet(LANG_KEY);
    return v && /^[a-z]{3}$/.test(v) ? v : null;
  }
  function blank() {
    return { audio_lang: null, subtitle_lang: null, subtitles_on: false, sub_style: null, search_lang: null, hide_spoilers: false };
  }
  function fromLocal() {
    var p = blank();
    p.sub_style = localStyle();
    p.search_lang = localLang();
    return p;
  }

  function request(method, body) {
    var options = { method: method, headers: {} };
    if (body) { options.headers["Content-Type"] = "application/json"; options.body = JSON.stringify(body); }
    return fetch("/api/nl/prefs", options).then(function (resp) {
      if (!resp.ok) { throw new Error("status " + resp.status); }
      return resp.json();
    });
  }

  function get() {
    if (cache) { return Promise.resolve(cache); }
    if (loading) { return loading; }
    loading = request("GET").then(function (data) {
      var prefs = Object.assign(blank(), data.prefs || {});
      var migrate = {};
      if (!prefs.sub_style && localStyle()) { migrate.sub_style = localStyle(); }
      if (!prefs.search_lang && localLang()) { migrate.search_lang = localLang(); }
      cache = prefs;
      if (Object.keys(migrate).length) {
        Object.assign(cache, migrate);
        request("PUT", migrate).then(function (d) { cache = Object.assign(blank(), d.prefs || {}); }).catch(function () { /* next load tries again */ });
      }
      return cache;
    }).catch(function () {
      cache = fromLocal();
      return cache;
    }).then(function (p) { loading = null; return p; });
    return loading;
  }

  function peek() { return cache || fromLocal(); }

  function save(patch) {
    if (!cache) { cache = fromLocal(); }
    Object.assign(cache, patch);
    return request("PUT", patch).then(function (d) {
      cache = Object.assign(blank(), d.prefs || {});
      return cache;
    }).catch(function () {
      // Server unreachable: keep the two M5 values on this device so they are not lost.
      if (patch.sub_style) { lsSet(STYLE_KEY, JSON.stringify(patch.sub_style)); }
      if (patch.search_lang) { lsSet(LANG_KEY, patch.search_lang); }
      return cache;
    });
  }

  global.NLPrefs = { get: get, peek: peek, save: save, languages: LANGUAGES };
})(window);
