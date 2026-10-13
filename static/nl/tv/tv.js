/* Northern Lights TV screens (M3): home, title, show and search for a remote control.
 * Same data as the phone pages (/api/nl/...), 10-foot layout, focus handled by focus.js.
 * Text from files and the network always goes in through textContent (NL.el). */
(function () {
  "use strict";
  var NL = window.NL;
  var el = NL.el;
  var body = document.body;
  var page = body.getAttribute("data-page");
  var itemId = body.getAttribute("data-item-id");
  var ROW_LIMIT = 80;      // cards per row on home; a longer shelf is a long walk with a remote
  var SEARCH_PER_ROW = 6;

  function setStatus(text) {
    var s = document.getElementById("tv-status");
    if (s) { s.textContent = text || ""; s.hidden = !text; }
  }

  /* The phone pages' links, pointed at the TV pages. Play links get tv=1 (remote-friendly player). */
  function tvHref(href) {
    if (!href) { return href; }
    if (href.indexOf("/nl/title/") === 0) { return "/nl/tv/title/" + href.slice("/nl/title/".length); }
    if (href.indexOf("/nl/show/") === 0) { return "/nl/tv/show/" + href.slice("/nl/show/".length); }
    if (href.indexOf("/nl/play/") === 0) { return href + (href.indexOf("?") < 0 ? "?" : "&") + "tv=1"; }
    return href;
  }

  function lsGet(k) { try { return window.localStorage.getItem(k); } catch (e) { return null; } }
  function lsSet(k, v) { try { window.localStorage.setItem(k, String(v)); } catch (e) { /* private mode */ } }

  function backdrop(hero, url) {
    if (!url) { return; }
    var back = el("div", "tv-backdrop");
    var img = document.createElement("img");
    img.alt = "";
    img.addEventListener("error", function () { back.remove(); });
    img.src = url;
    back.appendChild(img);
    hero.appendChild(back);
  }

  /* ----------------------------------------------------------------- cards */

  function posterCard(item) {
    var li = el("li", "tv-item");
    var a = el("a", "tv-card");
    a.href = tvHref(item.href);
    a.setAttribute("data-nl-focus", "");
    var art = el("div", "tv-art tv-poster");
    NL.fillImage(art, [item.poster_url, item.image_fallback], item.title);
    if (item.progress) {
      art.appendChild(NL.progressBar(item.progress));
    } else if (item.badge) {
      art.appendChild(el("span", "tv-badge", item.badge));
    }
    a.appendChild(art);
    a.appendChild(el("div", "tv-name", item.title));
    a.appendChild(el("div", "tv-meta", NL.metaLine(item)));
    var label = item.title + (item.year ? " (" + item.year + ")" : "");
    if (item.progress) { label += ", " + item.progress + " percent watched"; } else if (item.badge) { label += ", " + item.badge; }
    a.setAttribute("aria-label", label);
    li.appendChild(a);
    return li;
  }

  function wideCard(c) {
    var li = el("li", "tv-item tv-item-wide");
    var a = el("a", "tv-card");
    a.href = tvHref(c.href);
    a.setAttribute("data-nl-focus", "");
    var art = el("div", "tv-art tv-still");
    NL.fillImage(art, [c.image, c.poster_url], c.title);
    art.appendChild(NL.progressBar(c.progress));
    a.appendChild(art);
    a.appendChild(el("div", "tv-name", c.title));
    a.appendChild(el("div", "tv-meta", c.subtitle || ""));
    a.setAttribute("aria-label", "Continue " + c.title + (c.subtitle ? ", " + c.subtitle : "") + ", " + c.progress + " percent watched");
    li.appendChild(a);
    return li;
  }

  function fillRow(sectionId, listId, items, build) {
    var section = document.getElementById(sectionId);
    var list = document.getElementById(listId);
    if (!items || !items.length) { section.hidden = true; return false; }
    items.slice(0, ROW_LIMIT).forEach(function (it) { list.appendChild(build(it)); });
    section.hidden = false;
    return true;
  }

  /* ------------------------------------------------------------------ home */

  function initHome() {
    var home = NL.fetchJSON("/api/nl/home");
    // The home call only carries the newest few; the library calls give the whole shelf.
    var movies = NL.fetchJSON("/api/nl/library?type=movie").catch(function () { return null; });
    var shows = NL.fetchJSON("/api/nl/library?type=tv").catch(function () { return null; });
    Promise.all([home, movies, shows]).then(function (r) {
      var data = r[0];
      var any = false;
      any = fillRow("tv-continue", "tv-continue-list", data.continue_watching, wideCard) || any;
      any = fillRow("tv-movies", "tv-movies-list", (r[1] && r[1].items) || data.movies, posterCard) || any;
      any = fillRow("tv-shows", "tv-shows-list", (r[2] && r[2].items) || data.shows, posterCard) || any;
      setStatus(any ? "" : "Nothing on the shelf yet. Add something from the Download tab of the main app.");
      window.NLFocus.start();
    }).catch(function () {
      setStatus("Could not load the library. Is the app running?");
      window.NLFocus.start();
    });
  }

  /* ----------------------------------------------------------------- title */

  function button(text, href, primary) {
    var a = el("a", "tv-btn" + (primary ? " is-primary" : ""), text);
    a.href = tvHref(href);
    a.setAttribute("data-nl-focus", "");
    return a;
  }

  function heroShell(root, t) {
    root.textContent = "";
    var hero = el("div", "tv-hero");
    backdrop(hero, t.backdrop);
    var wrap = el("div", "tv-hero-body");
    var poster = el("div", "tv-art tv-poster tv-hero-poster");
    NL.fillImage(poster, [t.poster_url, t.image_fallback], t.title);
    wrap.appendChild(poster);
    var info = el("div", "tv-hero-info");
    info.appendChild(el("h1", "tv-title", t.title));
    return { hero: hero, wrap: wrap, info: info };
  }

  function genres(list) {
    var ul = el("ul", "tv-genres");
    (list || []).forEach(function (g) { ul.appendChild(el("li", null, g)); });
    return ul;
  }

  function renderTitle(t) {
    var root = document.getElementById("tv-detail");
    document.title = t.title + " - Northern Lights";
    var h = heroShell(root, t);
    var facts = [];
    if (t.year) { facts.push(String(t.year)); }
    if (t.media_type === "tv") {
      var n = (t.seasons || []).length;
      if (n) { facts.push(n + (n === 1 ? " season" : " seasons")); }
    } else if (t.runtime) {
      facts.push(NL.fmtRuntime(t.runtime));
    }
    h.info.appendChild(el("p", "tv-facts", facts.join(" · ")));
    if (t.genres && t.genres.length) { h.info.appendChild(genres(t.genres)); }
    if (t.overview) { h.info.appendChild(el("p", "tv-overview", t.overview)); }

    var actions = el("div", "tv-actions");
    actions.setAttribute("data-nl-row", "actions");
    if (t.media_type === "tv") {
      actions.appendChild(button("Episodes", t.show_href, true));
      if (t.seasons && t.seasons.length) {
        actions.appendChild(button("Start from S" + t.seasons[0].number + " · E1", t.play_href, false));
      }
    } else if (t.can_resume) {
      actions.appendChild(button("Resume from " + NL.fmtClock(t.resume_seconds), t.resume_href, true));
      actions.appendChild(button("Start over", t.restart_href, false));
    } else {
      actions.appendChild(button(t.played ? "Watch again" : "Play", t.play_href, true));
    }
    var subNote = el("p", "tv-meta");
    subNote.setAttribute("role", "status");
    subNote.hidden = true;
    if (t.media_type !== "tv" && window.NLSubtitles) {
      // D4: fix a missing or out-of-sync subtitle before pressing play.
      var find = el("button", "tv-btn", "Find subtitles");
      find.type = "button";
      find.setAttribute("data-nl-focus", "");
      find.addEventListener("click", function () {
        var host = document.getElementById("tv-subfind");
        if (!host) {
          host = el("div");
          host.id = "tv-subfind";
          host.hidden = true;
          document.body.appendChild(host);
        }
        window.NLSubtitles.open({
          container: host,
          itemId: t.id,
          tv: true,
          onDownloaded: function () {
            subNote.textContent = "Subtitles added. Pick them in the player’s Subtitles menu.";
            subNote.hidden = false;
          },
          onClose: function () { window.NLFocus.focus(find); }
        });
      });
      actions.appendChild(find);
    }
    h.info.appendChild(actions);
    h.info.appendChild(subNote);
    if (t.media_type !== "tv" && t.progress && !t.played) {
      var p = el("div", "tv-detail-progress");
      p.appendChild(NL.progressBar(t.progress));
      p.appendChild(el("span", "tv-meta", t.progress + "% watched"));
      h.info.appendChild(p);
    }
    h.wrap.appendChild(h.info);
    h.hero.appendChild(h.wrap);
    root.appendChild(h.hero);
    root.hidden = false;
    setStatus("");
    window.NLFocus.start();
  }

  function initTitle() {
    NL.fetchJSON("/api/nl/title/" + encodeURIComponent(itemId)).then(function (t) {
      // A show opened through the title URL belongs on the show screen.
      if (t.media_type === "tv" && t.show_href) { window.location.replace(tvHref(t.show_href)); return; }
      renderTitle(t);
    }).catch(function (err) {
      setStatus(err.status === 404 ? "That title isn’t in the library." : "Could not load this title. Is the app running?");
      window.NLFocus.start();
    });
  }

  /* ------------------------------------------------------------------ show */

  var show = null;
  var episodesBox = null;
  var seasonRow = null;

  function episodeCard(e) {
    var li = el("li", "tv-item tv-item-wide");
    var a = el("a", "tv-card" + (e.played ? " is-played" : ""));
    a.href = tvHref(e.href);
    a.setAttribute("data-nl-focus", "");
    var art = el("div", "tv-art tv-still");
    NL.fillImage(art, [e.image], e.code || ("E" + (e.episode || "")));
    if (e.progress) { art.appendChild(NL.progressBar(e.progress)); }
    if (e.played) {
      var tick = el("span", "tv-tick", "✓");
      tick.setAttribute("role", "img");
      tick.setAttribute("aria-label", "Watched");
      art.appendChild(tick);
    }
    a.appendChild(art);
    a.appendChild(el("div", "tv-name", (e.episode != null ? "E" + e.episode + " · " : "") + e.name));
    var meta = [];
    if (e.runtime_minutes) { meta.push(NL.fmtRuntime(e.runtime_minutes)); }
    if (e.progress && !e.played) { meta.push(e.progress + "% watched"); }
    a.appendChild(el("div", "tv-meta", meta.join(" · ")));
    a.setAttribute("aria-label", (e.code ? e.code + ", " : "") + e.name + (e.played ? ", watched" : "") +
      (e.progress && !e.played ? ", " + e.progress + " percent watched" : ""));
    li.appendChild(a);
    return li;
  }

  function showSeason(number) {
    lsSet("nl.season." + itemId, number);
    episodesBox.textContent = "";
    episodesBox._nlCol = 0;
    var sn = show.seasons.filter(function (s) { return s.number === number; })[0];
    (sn ? sn.episodes : []).forEach(function (e) { episodesBox.appendChild(episodeCard(e)); });
    // Open the season on the first episode you have not finished.
    if (sn) {
      for (var i = 0; i < sn.episodes.length; i++) {
        if (!sn.episodes[i].played) { episodesBox._nlCol = i; break; }
      }
    }
    if (seasonRow) {
      Array.prototype.forEach.call(seasonRow.querySelectorAll("button"), function (b) {
        b.setAttribute("aria-pressed", String(Number(b.getAttribute("data-season")) === number));
      });
    }
    episodesBox.scrollLeft = 0;
  }

  function autoplaySwitch() {
    var box = document.createElement("button");
    box.type = "button";
    box.className = "tv-btn tv-switch";
    box.setAttribute("data-nl-focus", "");
    box.setAttribute("role", "switch");
    var label = el("span", "tv-switch-label", "Autoplay next episode");
    var state = el("span", "tv-switch-state");
    box.appendChild(label);
    box.appendChild(state);
    function paint(on) {
      box.setAttribute("aria-checked", String(!!on));
      state.textContent = on ? "On" : "Off";
    }
    paint(show.autoplay);
    box.addEventListener("click", function () {
      var want = box.getAttribute("aria-checked") !== "true";
      paint(want);                                   // optimistic; rolled back on failure
      NL.fetchJSON("/api/nl/show/" + encodeURIComponent(itemId) + "/prefs", {
        method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ autoplay: want })
      }).then(function (r) { show.autoplay = r.autoplay; paint(r.autoplay); }).catch(function () { paint(!want); });
    });
    return box;
  }

  function renderShow(s) {
    show = s;
    var root = document.getElementById("tv-detail");
    document.title = s.title + " - Northern Lights";
    var h = heroShell(root, s);
    var facts = [];
    if (s.year) { facts.push(String(s.year)); }
    facts.push(s.seasons.length + (s.seasons.length === 1 ? " season" : " seasons"));
    facts.push(s.episode_count + (s.episode_count === 1 ? " episode" : " episodes"));
    h.info.appendChild(el("p", "tv-facts", facts.join(" · ")));
    if (s.genres && s.genres.length) { h.info.appendChild(genres(s.genres)); }
    if (s.overview) { h.info.appendChild(el("p", "tv-overview", s.overview)); }

    var actions = el("div", "tv-actions");
    actions.setAttribute("data-nl-row", "actions");
    if (s.next_up) { actions.appendChild(button(s.next_up.text, s.next_up.href, true)); }
    actions.appendChild(autoplaySwitch());
    h.info.appendChild(actions);
    if (!s.jellyfin) {
      h.info.appendChild(el("p", "tv-meta", "Watched ticks and Next up need Jellyfin, which isn’t answering right now."));
    }
    h.wrap.appendChild(h.info);
    h.hero.appendChild(h.wrap);
    root.appendChild(h.hero);

    var section = el("section", "tv-section");
    section.setAttribute("data-nl-section", "");
    section.appendChild(el("h2", null, "Episodes"));
    if (s.seasons.length > 1) {
      seasonRow = el("div", "tv-seasons");
      seasonRow.setAttribute("data-nl-row", "seasons");
      seasonRow.setAttribute("role", "group");
      seasonRow.setAttribute("aria-label", "Season");
      s.seasons.forEach(function (sn) {
        var b = el("button", "tv-pill", sn.label);
        b.type = "button";
        b.setAttribute("data-nl-focus", "");
        b.setAttribute("data-season", sn.number);
        b.addEventListener("click", function () { showSeason(sn.number); });
        seasonRow.appendChild(b);
      });
      section.appendChild(seasonRow);
    }
    episodesBox = el("ul", "tv-row");
    episodesBox.setAttribute("data-nl-row", "episodes");
    section.appendChild(episodesBox);
    root.appendChild(section);

    var start = s.seasons[0] ? s.seasons[0].number : 0;
    var saved = Number(lsGet("nl.season." + itemId));
    if (s.seasons.some(function (x) { return x.number === saved; }) && !s.next_up) { start = saved; }
    if (s.next_up) { start = s.next_up.season; }
    showSeason(start);
    root.hidden = false;
    setStatus("");
    window.NLFocus.start();
  }

  function initShow() {
    NL.fetchJSON("/api/nl/show/" + encodeURIComponent(itemId)).then(renderShow).catch(function (err) {
      setStatus(err.status === 404 ? "That show isn’t in the library." : "Could not load this show. Is the app running?");
      window.NLFocus.start();
    });
  }

  /* ---------------------------------------------------------------- search */

  function resultCard(r) {
    var ready = r.ready && r.href;
    var li = el("li", "tv-item");
    var a = el(ready ? "a" : "div", "tv-card" + (ready ? "" : " is-away"));
    if (ready) {
      a.href = tvHref(r.href);
      a.setAttribute("data-nl-focus", "");
    }
    var art = el("div", "tv-art tv-poster");
    NL.fillImage(art, [r.poster_url], r.title);
    art.appendChild(el("span", "tv-badge" + (ready ? "" : " is-away"), r.label));
    a.appendChild(art);
    a.appendChild(el("div", "tv-name", r.title));
    var sub = r.kind === "episode" ? r.subtitle : [r.kind === "show" ? "Show" : "Movie", r.year].filter(Boolean).join(" · ");
    a.appendChild(el("div", "tv-meta", sub));
    a.setAttribute("aria-label", r.title + ", " + r.label);
    li.appendChild(a);
    return li;
  }

  function initSearch() {
    var input = document.getElementById("tv-q");
    var out = document.getElementById("tv-results");
    var hint = document.getElementById("tv-search-hint");
    var seq = 0;
    window.NLFocus.remember(false);
    setStatus("");

    function show(results) {
      out.textContent = "";
      if (!results.length) {
        hint.textContent = "Nothing matches that.";
        return;
      }
      hint.textContent = "";
      for (var i = 0; i < results.length; i += SEARCH_PER_ROW) {
        var ul = el("ul", "tv-row tv-row-grid");
        ul.setAttribute("data-nl-row", "results-" + (i / SEARCH_PER_ROW));
        results.slice(i, i + SEARCH_PER_ROW).forEach(function (r) { ul.appendChild(resultCard(r)); });
        out.appendChild(ul);
      }
      var first = out.querySelector("[data-nl-focus]");
      if (first) { window.NLFocus.focus(first); }
    }

    function run() {
      var q = input.value.trim();
      if (q.length < 2) { out.textContent = ""; hint.textContent = "Type at least two letters."; return; }
      var mine = ++seq;
      hint.textContent = "Searching…";
      NL.fetchJSON("/api/nl/search?q=" + encodeURIComponent(q)).then(function (data) {
        if (mine === seq) { show(data.results || []); }
      }).catch(function () {
        if (mine === seq) { out.textContent = ""; hint.textContent = "Search isn’t available right now."; }
      });
    }

    input.addEventListener("nl-input-done", run);
    document.getElementById("tv-search-form").addEventListener("submit", function (e) { e.preventDefault(); run(); });
    window.NLFocus.start();
  }

  /* ------------------------------------------------------------- who, me */

  function initWho() {
    setStatus("");
    window.NLFocus.remember(false);
    window.NLFocus.start();
  }

  function langName(code) {
    var list = window.NLPrefs.languages;
    for (var i = 0; i < list.length; i++) { if (list[i][0] === code) { return list[i][1]; } }
    return code ? code.toUpperCase() : "No preference";
  }
  // OK steps through "No preference" then each language; a D-pad has no dropdown.
  function nextLang(code) {
    var codes = [""].concat(window.NLPrefs.languages.map(function (l) { return l[0]; }));
    var at = codes.indexOf(code || "");
    return codes[(at + 1) % codes.length];
  }

  function initMe() {
    var audio = document.getElementById("tv-pref-audio");
    var sub = document.getElementById("tv-pref-sub");
    var on = document.getElementById("tv-pref-on");
    var status = document.getElementById("tv-pref-status");
    setStatus("");
    var prefs = null;
    function paint() {
      audio.textContent = "Audio language: " + langName(prefs.audio_lang);
      sub.textContent = "Subtitle language: " + langName(prefs.subtitle_lang);
      on.textContent = "";
      on.appendChild(document.createTextNode("Subtitles on by default "));
      on.appendChild(el("span", "tv-switch-state", prefs.subtitles_on ? "On" : "Off"));
      on.setAttribute("aria-checked", String(!!prefs.subtitles_on));
    }
    function save(patch) {
      Object.assign(prefs, patch);
      paint();
      status.textContent = "Saving…";
      window.NLPrefs.save(patch).then(function () { status.textContent = "Saved"; });
    }
    window.NLPrefs.get().then(function (p) {
      prefs = Object.assign({}, p);
      paint();
      window.NLFocus.start();
    });
    audio.addEventListener("click", function () { if (prefs) { save({ audio_lang: nextLang(prefs.audio_lang) || null }); } });
    sub.addEventListener("click", function () { if (prefs) { save({ subtitle_lang: nextLang(prefs.subtitle_lang) || null }); } });
    on.addEventListener("click", function () { if (prefs) { save({ subtitles_on: !prefs.subtitles_on }); } });
  }

  /* ------------------------------------------------------------------ init */

  if (page === "home") { initHome(); }
  else if (page === "title") { initTitle(); }
  else if (page === "show") { initShow(); }
  else if (page === "search") { initSearch(); }
  else if (page === "who") { initWho(); }
  else if (page === "me") { initMe(); }
})();
