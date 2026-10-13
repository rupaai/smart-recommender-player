/* Northern Lights "Find subtitles" panel (M5, D1 D2 D3 D4).
 * One panel for the player (web and TV) and for the title pages (web and TV). It only talks to our
 * own API (/api/nl/subtitles/{id}); Jellyfin and its key stay on the server.
 *
 *   NLSubtitles.open({
 *     container: element,      // an empty element; it becomes the overlay (class nls-host) while open
 *     itemId: 12,              // our media item id
 *     sourceId: null,          // media_sources.id for one episode of a show
 *     tv: false,               // big type and remote-control focus look
 *     onDownloaded: fn(result) // after Jellyfin saved the subtitle; may return a promise (the panel waits, then closes)
 *     onClose: fn()
 *   });
 *
 * The search language is the person's (NLPrefs, M4: saved on the server per person; localStorage nl.subLang
 * is only the fallback / migration source), else their preferred subtitle language, else English.
 * Everything that comes from the network goes in
 * through textContent. While open, the panel handles every key itself (arrows, OK, Back). */
(function (global) {
  "use strict";

  var LANGS = [
    ["eng", "English"], ["spa", "Spanish"], ["fra", "French"], ["deu", "German"], ["ita", "Italian"],
    ["por", "Portuguese"], ["nld", "Dutch"], ["swe", "Swedish"], ["nor", "Norwegian"], ["dan", "Danish"],
    ["fin", "Finnish"], ["pol", "Polish"], ["rus", "Russian"], ["ukr", "Ukrainian"], ["tur", "Turkish"],
    ["ell", "Greek"], ["ces", "Czech"], ["hun", "Hungarian"], ["ron", "Romanian"], ["heb", "Hebrew"],
    ["ara", "Arabic"], ["fas", "Persian"], ["hin", "Hindi"], ["ben", "Bengali"], ["urd", "Urdu"],
    ["tam", "Tamil"], ["zho", "Chinese"], ["jpn", "Japanese"], ["kor", "Korean"], ["tha", "Thai"],
    ["vie", "Vietnamese"], ["ind", "Indonesian"]
  ];
  var DEFAULT_LANG = "eng";
  var LANG_KEY = "nl.subLang";
  var POLL_NOTE = "Searching OpenSubtitles through Jellyfin…";
  var current = null;

  function lsGet(k) { try { return global.localStorage.getItem(k); } catch (e) { return null; } }
  function lsSet(k, v) { try { global.localStorage.setItem(k, String(v)); } catch (e) { /* private mode */ } }

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) { node.className = className; }
    if (text !== undefined && text !== null) { node.textContent = text; }
    return node;
  }

  function langName(code) {
    for (var i = 0; i < LANGS.length; i++) { if (LANGS[i][0] === code) { return LANGS[i][1]; } }
    return code.toUpperCase();
  }
  function savedLang() {
    var p = global.NLPrefs ? global.NLPrefs.peek() : null;
    var v = (p && (p.search_lang || p.subtitle_lang)) || lsGet(LANG_KEY);
    return v && /^[a-z]{3}$/.test(v) ? v : DEFAULT_LANG;
  }

  function isBack(e) {
    var k = e.key, c = e.keyCode;
    return k === "Escape" || k === "Backspace" || k === "GoBack" || k === "BrowserBack" || k === "XF86Back" ||
      c === 4 || c === 27 || c === 8 || c === 461 || c === 10009;
  }
  function isOk(e) { return e.key === "Enter" || e.key === " " || e.keyCode === 23 || e.keyCode === 66 || e.keyCode === 13; }

  function request(url, options) {
    return fetch(url, options).then(function (resp) {
      return resp.json().catch(function () { return {}; }).then(function (body) {
        if (!resp.ok) {
          var err = new Error(body.detail || "Something went wrong.");
          err.code = body.code || "";
          throw err;
        }
        return body;
      });
    }, function () {
      var err = new Error("Can’t reach the movie server. Is mars on?");
      err.code = "unreachable";
      throw err;
    });
  }

  function open(opts) {
    if (current) { current.close(); }
    var host = opts.container;
    var tv = !!opts.tv;
    var state = { view: "results", lang: savedLang(), results: [], status: null, busy: false, seq: 0, closed: false };

    host.textContent = "";
    host.className = "nls-host" + (tv ? " nls-tv" : "");
    host.hidden = false;
    var panel = el("div", "nls-panel");
    panel.setAttribute("role", "dialog");
    panel.setAttribute("aria-modal", "true");
    panel.setAttribute("aria-label", "Find subtitles");
    host.appendChild(panel);

    function url(lang) {
      return "/api/nl/subtitles/" + encodeURIComponent(opts.itemId) + "?language=" + encodeURIComponent(lang) +
        (opts.sourceId ? "&source=" + encodeURIComponent(opts.sourceId) : "");
    }

    function buttons() {
      return Array.prototype.slice.call(panel.querySelectorAll("button:not([disabled])"));
    }
    function focusFirst() {
      var first = panel.querySelector(".nls-row") || panel.querySelector(".nls-lang") || panel.querySelector("button");
      if (first) { first.focus(); }
    }

    function chip(text, cls) { return el("span", "nls-chip" + (cls ? " " + cls : ""), text); }

    function resultRow(r) {
      var b = el("button", "nls-row");
      b.type = "button";
      b.appendChild(el("span", "nls-name", r.name || (r.language_name + " subtitles")));
      var meta = el("span", "nls-meta");
      if (r.hash_match) { meta.appendChild(chip("Exact match", "nls-badge")); }
      if (r.language_name) { meta.appendChild(chip(r.language_name)); }
      if (r.hearing_impaired) { meta.appendChild(chip("SDH")); }
      if (r.forced) { meta.appendChild(chip("Forced")); }
      if (r.format) { meta.appendChild(chip(String(r.format).toUpperCase())); }
      var bits = [];
      if (r.provider) { bits.push(r.provider); }
      if (typeof r.downloads === "number") { bits.push("↓ " + r.downloads.toLocaleString("en-US")); }
      if (typeof r.rating === "number" && r.rating > 0) { bits.push("★ " + r.rating.toFixed(1)); }
      if (bits.length) { meta.appendChild(el("span", "nls-bits", bits.join(" · "))); }
      b.appendChild(meta);
      b.setAttribute("aria-label", (r.hash_match ? "Exact match. " : "") + (r.name || r.language_name) +
        (r.hearing_impaired ? ", SDH" : "") + (r.forced ? ", forced" : "") + (r.provider ? ", " + r.provider : ""));
      b.addEventListener("click", function () { download(r); });
      return b;
    }

    function render(keepFocus) {
      panel.textContent = "";
      var head = el("div", "nls-head");
      head.appendChild(el("h2", "nls-title", state.view === "languages" ? "Language" : "Find subtitles"));
      var tools = el("div", "nls-tools");
      if (state.view === "results") {
        var lang = el("button", "nls-btn nls-lang", "Language: " + langName(state.lang));
        lang.type = "button";
        lang.disabled = state.busy;
        lang.addEventListener("click", function () { state.view = "languages"; render(); });
        tools.appendChild(lang);
      }
      var close = el("button", "nls-btn nls-close", state.view === "languages" ? "Back" : "Close");
      close.type = "button";
      close.addEventListener("click", function () { if (state.view === "languages") { state.view = "results"; render(); } else { api.close(); } });
      tools.appendChild(close);
      head.appendChild(tools);
      panel.appendChild(head);

      if (state.view === "languages") {
        var list = el("div", "nls-list");
        list.setAttribute("role", "listbox");
        LANGS.forEach(function (l) {
          var b = el("button", "nls-row nls-lang-row");
          b.type = "button";
          b.setAttribute("role", "option");
          b.setAttribute("aria-selected", String(l[0] === state.lang));
          b.appendChild(el("span", "nls-tick", l[0] === state.lang ? "✓" : ""));
          b.appendChild(el("span", "nls-name", l[1]));
          b.addEventListener("click", function () {
            state.lang = l[0];
            if (global.NLPrefs) { global.NLPrefs.save({ search_lang: l[0] }); } else { lsSet(LANG_KEY, l[0]); }
            state.view = "results";
            search();
          });
          list.appendChild(b);
        });
        panel.appendChild(list);
        if (!keepFocus) {
          var sel = panel.querySelector("[aria-selected='true']") || panel.querySelector(".nls-row");
          if (sel) { sel.focus(); try { sel.scrollIntoView({ block: "center" }); } catch (e) { /* old browser */ } }
        }
        return;
      }

      if (state.status) {
        var st = el("p", "nls-status nls-" + state.status.kind, state.status.text);
        st.setAttribute("role", state.status.kind === "error" ? "alert" : "status");
        panel.appendChild(st);
        if (state.status.retry) {
          var again = el("button", "nls-btn nls-retry", "Try again");
          again.type = "button";
          again.addEventListener("click", search);
          panel.appendChild(again);
        }
      }
      if (state.results.length) {
        var rows = el("div", "nls-list");
        state.results.forEach(function (r) {
          var row = resultRow(r);
          if (state.busy) { row.disabled = true; }
          rows.appendChild(row);
        });
        panel.appendChild(rows);
      }
      if (!keepFocus) { focusFirst(); }
    }

    function search() {
      var mine = ++state.seq;
      state.view = "results";
      state.results = [];
      state.busy = false;
      state.status = { kind: "busy", text: POLL_NOTE };
      render();
      request(url(state.lang)).then(function (data) {
        if (state.closed || mine !== state.seq) { return; }
        state.results = data.results || [];
        state.status = state.results.length
          ? { kind: "info", text: state.results.length + " found for " + (data.language_name || langName(state.lang)) + ". Exact matches are listed first." }
          : { kind: "error", text: data.message || ("No subtitles found for " + langName(state.lang) + "."), retry: false };
        render();
      }).catch(function (err) {
        if (state.closed || mine !== state.seq) { return; }
        state.status = { kind: "error", text: err.message, retry: err.code === "failed" || err.code === "unreachable" || err.code === "unavailable" };
        render();
      });
    }

    function download(r) {
      if (state.busy) { return; }
      state.busy = true;
      state.status = { kind: "busy", text: "Downloading “" + (r.name || r.language_name) + "”…" };
      render(true);
      var mine = ++state.seq;
      request("/api/nl/subtitles/" + encodeURIComponent(opts.itemId) + (opts.sourceId ? "?source=" + encodeURIComponent(opts.sourceId) : ""), {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ subtitle_id: r.id })
      }).then(function () {
        if (state.closed || mine !== state.seq) { return; }
        state.status = { kind: "busy", text: "Added. Loading it…" };
        render(true);
        return Promise.resolve(opts.onDownloaded ? opts.onDownloaded(r) : null).then(function () {
          api.close();
        }, function (err) {
          if (state.closed) { return; }
          state.busy = false;
          state.status = { kind: "error", text: (err && err.message) || "The subtitle was saved, but it didn’t show up yet. Look in the Subtitles menu in a moment.", retry: false };
          render();
        });
      }).catch(function (err) {
        if (state.closed || mine !== state.seq) { return; }
        state.busy = false;
        state.status = { kind: "error", text: err.message, retry: false };
        render();
      });
    }

    function onKey(e) {
      if (e.ctrlKey || e.metaKey || e.altKey) { return; }
      e.stopImmediatePropagation();           // the player and the TV focus manager must not see these keys
      if (e.key === "Tab") { return; }
      if (isBack(e)) {
        e.preventDefault();
        if (e.repeat) { return; }
        if (state.view === "languages") { state.view = "results"; render(); } else if (!state.busy) { api.close(); }
        return;
      }
      var dir = { ArrowLeft: -1, ArrowUp: -1, ArrowRight: 1, ArrowDown: 1 }[e.key];
      if (dir) {
        e.preventDefault();
        var list = buttons();
        if (!list.length) { return; }
        var i = list.indexOf(document.activeElement);
        var next = i < 0 ? 0 : Math.max(0, Math.min(list.length - 1, i + dir));
        list[next].focus();
        try { list[next].scrollIntoView({ block: "nearest" }); } catch (err) { /* old browser */ }
        return;
      }
      if (isOk(e)) {
        e.preventDefault();
        if (e.repeat) { return; }
        var a = document.activeElement;
        if (a && panel.contains(a) && a.tagName === "BUTTON") { a.click(); } else { focusFirst(); }
      }
    }
    function onKeyUp(e) { if (e.key === " " || isOk(e)) { e.stopImmediatePropagation(); e.preventDefault(); } }
    function onBackdrop(e) { if (e.target === host && !state.busy) { api.close(); } }

    global.addEventListener("keydown", onKey, true);
    global.addEventListener("keyup", onKeyUp, true);
    host.addEventListener("click", onBackdrop);

    var api = {
      close: function () {
        if (state.closed) { return; }
        state.closed = true;
        global.removeEventListener("keydown", onKey, true);
        global.removeEventListener("keyup", onKeyUp, true);
        host.removeEventListener("click", onBackdrop);
        host.hidden = true;
        host.textContent = "";
        if (current === api) { current = null; }
        if (opts.onClose) { try { opts.onClose(); } catch (e) { console.error(e); } }
      }
    };
    current = api;
    search();
    return api;
  }

  global.NLSubtitles = { open: open, languages: LANGS, savedLanguage: savedLang };
})(window);
