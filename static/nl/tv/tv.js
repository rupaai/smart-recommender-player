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
    if (item.together && window.NLCouple) { art.appendChild(window.NLCouple.togetherBadge(true)); }   // M9a
    a.appendChild(art);
    a.appendChild(el("div", "tv-name", item.title));
    a.appendChild(el("div", "tv-meta", NL.metaLine(item)));
    var label = item.title + (item.year ? " (" + item.year + ")" : "");
    if (item.progress) { label += ", " + item.progress + " percent watched"; } else if (item.badge) { label += ", " + item.badge; }
    if (item.together) { label += ", watched together"; }
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

  function libraryUrl(type, unwatched) {
    return "/api/nl/library?type=" + type + (unwatched ? "&unwatched=1" : "");
  }

  function emptyRow(sectionId, listId) {
    document.getElementById(listId).textContent = "";
    document.getElementById(sectionId).hidden = true;
  }

  function initHome() {
    var unwatchedOn = false;
    var home = NL.fetchJSON("/api/nl/home");
    // M7b: "Unwatched only" for the Movies and Shows rows, remembered between visits.
    var toggle = window.NLPolish ? window.NLPolish.unwatchedSwitch({ key: "nl.tv.unwatched", tv: true, onChange: refill }) : null;
    unwatchedOn = !!(toggle && toggle.nlOn);
    // The home call only carries the newest few; the library calls give the whole shelf.
    var movies = NL.fetchJSON(libraryUrl("movie", unwatchedOn)).catch(function () { return null; });
    var shows = NL.fetchJSON(libraryUrl("tv", unwatchedOn)).catch(function () { return null; });

    function fillLibrary(m, s, unwatched, fallback) {
      var note = document.getElementById("tv-filters-note");
      var applied = !!((m && m.unwatched_applied) || (s && s.unwatched_applied));
      var any = false;
      emptyRow("tv-movies", "tv-movies-list");
      emptyRow("tv-shows", "tv-shows-list");
      var movieRow = fillRow("tv-movies", "tv-movies-list", (m && m.items) || (fallback ? fallback.movies : []), posterCard);
      var showRow = fillRow("tv-shows", "tv-shows-list", (s && s.items) || (fallback ? fallback.shows : []), posterCard);
      any = movieRow || showRow || any;
      if (!unwatched) { note.textContent = ""; }
      else if (!m && !s) { note.textContent = "Could not load the list."; }
      else if (!applied) { note.textContent = "Jellyfin isn’t answering, so I can’t tell what you’ve watched. Showing everything."; }
      else if (!movieRow && !showRow) { note.textContent = "You’ve watched everything on the shelf."; }
      else if (!movieRow) { note.textContent = "You’ve watched every movie on the shelf."; }
      else if (!showRow) { note.textContent = "You’ve watched every show on the shelf."; }
      else { note.textContent = ""; }
      return any;
    }

    var latest = null;
    function refill(on) {
      unwatchedOn = on;
      Promise.all([
        NL.fetchJSON(libraryUrl("movie", on)).catch(function () { return null; }),
        NL.fetchJSON(libraryUrl("tv", on)).catch(function () { return null; })
      ]).then(function (r) {
        fillLibrary(r[0], r[1], on, on ? null : latest);
        // The filter pill may have been the only thing still on screen; keep the focus on it.
        if (toggle) { window.NLFocus.focus(toggle); }
      });
    }

    Promise.all([home, movies, shows]).then(function (r) {
      var data = r[0];
      latest = data;
      var any = false;
      any = fillRow("tv-continue", "tv-continue-list", data.continue_watching, wideCard) || any;
      initPicks();
      any = fillRow("tv-recent-added", "tv-recent-added-list", data.recently_added, posterCard) || any;
      any = fillRow("tv-recent-watched", "tv-recent-watched-list", data.recently_watched, posterCard) || any;
      if (toggle) {
        document.getElementById("tv-filters-box").appendChild(toggle);
        document.getElementById("tv-filters").hidden = false;
      }
      any = fillLibrary(r[1], r[2], unwatchedOn, data) || any;
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

  /* M6: one focusable button per person; OK steps that person's answer through yes, maybe, pass, none. */
  function addInterest(parent, id) {
    if (!window.NLWatch) { return; }
    var row = el("div", "tv-interest");
    row.setAttribute("data-nl-row", "interest");
    row.setAttribute("role", "group");
    row.setAttribute("aria-label", "Would you watch this?");
    parent.appendChild(row);
    window.NLWatch.load().then(function (st) {
      if (!st) { return; }
      st.people.forEach(function (p) {
        if (!p.mine) {
          var read = el("span", "tv-wl-read", "");
          row.appendChild(read);
          var a = window.NLWatch.answers(id).filter(function (x) { return x.person === p.id; })[0];
          read.textContent = p.name + ": " + (a && a.state ? a.state.charAt(0).toUpperCase() + a.state.slice(1) : "Not yet");
          return;
        }
        var b = window.NLWatch.cycleButton(id, p.id, "tv-btn");
        b.setAttribute("data-nl-focus", "");
        row.appendChild(b);
      });
    });
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
    if (window.NLWatch) { actions.appendChild(window.NLWatch.fullControl(t.id, t.title, "tv-btn")); }
    if (window.NLPolish) {
      // M7b: keep this title off this viewer's home (or bring it back). A button in the existing actions row.
      actions.appendChild(window.NLPolish.hideToggle(t, {
        tv: true,
        onChange: function (hidden) {
          subNote.textContent = hidden === null ? "Could not change that. Try again."
            : hidden ? "Hidden from your home page. It is still in search." : "Back on your home page.";
          subNote.hidden = false;
        }
      }));
    }
    h.info.appendChild(actions);
    h.info.appendChild(subNote);
    var trailerNote = el("p", "tv-trailer-note");
    trailerNote.hidden = true;
    h.info.appendChild(trailerNote);
    addInterest(h.info, t.id);
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
    if (window.NLPolish) {
      // M7b: Cast & crew, Tech details (collapsed; OK opens them), the Trailer line and More like this.
      var extras = el("div", "tv-extras");
      root.appendChild(extras);
      window.NLPolish.titleExtras(t, extras, null, { tv: true, trailerNote: trailerNote, buildCard: posterCard });
    }
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
  var curSeason = null;
  var unwatchMode = null;        // M9a: {person, label} while "Mark unwatched" waits for an episode
  var unwatchHint = null;

  // M9c (W11): with spoiler hiding on, an unstarted episode has a blurred picture until OK is pressed on it once.
  function spoilerHidden(e) {
    return !!show.hide_spoilers && !e.played && !e.progress && !e.revealed;
  }

  function episodeCard(e) {
    var li = el("li", "tv-item tv-item-wide");
    var hidden = spoilerHidden(e);
    var a = el("a", "tv-card" + (e.played ? " is-played" : "") + (hidden ? " is-spoiler" : ""));
    a.href = tvHref(e.href);
    a.setAttribute("data-nl-focus", "");
    a.addEventListener("click", function (ev) {
      if (unwatchMode) {
        ev.preventDefault();
        markUnwatched(e);
        return;
      }
      if (hidden) {
        ev.preventDefault();
        e.revealed = true;
        var col = episodesBox._nlCol;
        var at = Array.prototype.indexOf.call(episodesBox.children, li);
        showSeason(curSeason);
        var again = episodesBox.querySelectorAll("[data-nl-focus]")[at >= 0 ? at : Math.min(col, episodesBox.children.length - 1)];
        if (again) { window.NLFocus.focus(again); }
      }
    });
    var art = el("div", "tv-art tv-still");
    NL.fillImage(art, [e.image], e.code || ("E" + (e.episode || "")));
    if (e.progress) { art.appendChild(NL.progressBar(e.progress)); }
    if (e.together && window.NLCouple) { art.appendChild(window.NLCouple.togetherBadge(true)); }   // M9a
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
    if (hidden) { meta.push("Details hidden, OK to show"); }
    a.appendChild(el("div", "tv-meta", meta.join(" · ")));
    a.setAttribute("aria-label", (e.code ? e.code + ", " : "") + e.name + (e.played ? ", watched" : "") +
      (e.progress && !e.played ? ", " + e.progress + " percent watched" : "") + (hidden ? ", details hidden, press OK to show" : ""));
    li.appendChild(a);
    return li;
  }

  function setUnwatchMode(mode) {
    unwatchMode = mode;
    if (unwatchHint) {
      unwatchHint.hidden = !mode;
      unwatchHint.textContent = mode ? "Pick the episode, then press OK. " + mode.label + ". Back cancels." : "";
    }
  }

  function markUnwatched(e) {
    var mode = unwatchMode;
    if (!e.played && !e.progress) {
      unwatchHint.textContent = "That episode isn\u2019t marked as watched. Pick another, or press Back.";
      return;
    }
    setUnwatchMode(null);
    unwatchHint.hidden = false;
    unwatchHint.textContent = "Working\u2026";
    window.NLCouple.unwatch(itemId, e.source_id, mode.person).then(function () {
      e.played = false; e.progress = 0; e.resume_seconds = 0; e.together = false;
      unwatchHint.textContent = "Marked unwatched.";
      var col = episodesBox._nlCol;
      showSeason(curSeason);
      var again = episodesBox.querySelectorAll("[data-nl-focus]")[Math.min(col, episodesBox.children.length - 1)];
      if (again) { window.NLFocus.focus(again); }
    }).catch(function () {
      unwatchHint.textContent = "Couldn\u2019t mark that unwatched. Is Jellyfin reachable?";
    });
  }

  // U6: one button per person who can be reset ("Mark unwatched for me", or each name on Both of us).
  function unwatchButtons(actions) {
    unwatchHint = el("p", "tv-unwatch-hint");
    unwatchHint.setAttribute("role", "status");
    unwatchHint.hidden = true;
    window.NLCouple.unwatchLabels(show.unwatch_people).forEach(function (entry) {
      var b = el("button", "tv-btn", entry.label);
      b.type = "button";
      b.setAttribute("data-nl-focus", "");
      b.addEventListener("click", function () {
        setUnwatchMode(unwatchMode && unwatchMode.person === entry.person ? null : entry);
        if (unwatchMode) {
          var first = episodesBox.querySelector("[data-nl-focus]");
          if (first) { window.NLFocus.focus(first); }
        }
      });
      actions.appendChild(b);
    });
  }

  // M9a: "We watch this together", only with two people configured.
  function pactSwitch() {
    var pact = show.pact;
    if (!pact || !pact.available) { return null; }
    var box = document.createElement("button");
    box.type = "button";
    box.className = "tv-btn tv-switch";
    box.setAttribute("data-nl-focus", "");
    box.setAttribute("role", "switch");
    var state = el("span", "tv-switch-state");
    box.appendChild(el("span", "tv-switch-label", "We watch this together"));
    box.appendChild(state);
    function paint(on) { box.setAttribute("aria-checked", String(!!on)); state.textContent = on ? "On" : "Off"; }
    paint(pact.together);
    box.addEventListener("click", function () {
      var want = box.getAttribute("aria-checked") !== "true";
      paint(want);
      NL.fetchJSON("/api/nl/show/" + encodeURIComponent(itemId) + "/pact", {
        method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ together: want })
      }).then(function (r) { pact.together = r.together; paint(r.together); }).catch(function () { paint(!want); });
    });
    return box;
  }

  function showSeason(number) {
    curSeason = number;
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
    var pact = window.NLCouple ? pactSwitch() : null;
    if (pact) { actions.appendChild(pact); }
    if (window.NLWatch) { actions.appendChild(window.NLWatch.fullControl(s.id, s.title, "tv-btn")); }
    h.info.appendChild(actions);
    addInterest(h.info, s.id);
    if (s.together_count) {
      h.info.appendChild(el("p", "tv-meta", "Watched together: " + s.together_count + (s.together_count === 1 ? " episode" : " episodes")));
    }
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
    if (window.NLCouple && s.seasons.some(function (sn) { return sn.episodes.some(function (x) { return x.played || x.progress; }); })) {
      var tools = el("div", "tv-seasons");
      tools.setAttribute("data-nl-row", "episode-tools");
      unwatchButtons(tools);
      section.appendChild(tools);
      section.appendChild(unwatchHint);
    }
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
    window.NLFocus.onBack(function () {
      if (!unwatchMode) { return false; }
      setUnwatchMode(null);
      return true;
    });
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

    // M8b: the phone as keyboard. What it types lands in the box and searches after a short pause; its Search button
    // searches at once. The box keeps its place on the remote's focus ring; only its text changes.
    var phoneTimer = null;
    if (window.NLPhoneKeyboard) {
      window.NLPhoneKeyboard.start({
        panel: document.getElementById("tv-phone"),
        qr: document.getElementById("tv-phone-qr"),
        code: document.getElementById("tv-phone-code"),
        how: document.getElementById("tv-phone-how"),
        onText: function (text) {
          input.value = text;
          window.clearTimeout(phoneTimer);
          phoneTimer = window.setTimeout(run, 450);
        },
        onSubmit: function () { window.clearTimeout(phoneTimer); run(); }
      });
    }
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
    var spoilers = document.getElementById("tv-pref-spoilers");
    setStatus("");
    var vetoes = document.getElementById("tv-vetoes");
    if (vetoes && window.NLChoose) { window.NLChoose.vetoList(vetoes, true); }   // M9b: bring back what was vetoed
    var prefs = null;
    function paint() {
      audio.textContent = "Audio language: " + langName(prefs.audio_lang);
      sub.textContent = "Subtitle language: " + langName(prefs.subtitle_lang);
      on.textContent = "";
      on.appendChild(document.createTextNode("Subtitles on by default "));
      on.appendChild(el("span", "tv-switch-state", prefs.subtitles_on ? "On" : "Off"));
      on.setAttribute("aria-checked", String(!!prefs.subtitles_on));
      spoilers.textContent = "";
      spoilers.appendChild(document.createTextNode("Hide spoilers in episode lists "));
      spoilers.appendChild(el("span", "tv-switch-state", prefs.hide_spoilers ? "On" : "Off"));
      spoilers.setAttribute("aria-checked", String(!!prefs.hide_spoilers));
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
    spoilers.addEventListener("click", function () { if (prefs) { save({ hide_spoilers: !prefs.hide_spoilers }); } });
  }

  /* ----------------------------------------- picks for two (M6): home row */

  function pickCard(item) {
    var li = el("li", "tv-item tv-item-pick");
    var a = el("a", "tv-card");
    a.href = tvHref(item.href);
    a.setAttribute("data-nl-focus", "");
    var art = el("div", "tv-art tv-poster");
    NL.fillImage(art, [item.poster_url, item.image_fallback], item.title);
    a.appendChild(art);
    a.appendChild(el("div", "tv-name", item.title));
    a.appendChild(el("div", "tv-meta", NL.metaLine(item)));
    a.appendChild(el("p", "tv-reason", item.reason || ""));
    a.setAttribute("aria-label", item.title + (item.year ? " (" + item.year + ")" : "") + ". " + (item.reason || ""));
    li.appendChild(a);
    return li;
  }

  function initPicks() {
    var section = document.getElementById("tv-picks");
    if (!section) { return; }
    var list = document.getElementById("tv-picks-list");
    var chipsRow = document.getElementById("tv-picks-chips");
    var heading = document.getElementById("tv-picks-h");
    var note = document.getElementById("tv-picks-note");
    var chipsBox = null;
    var value = window.NLWatch ? window.NLWatch.savedRuntime() : null;
    var seq = 0;

    function render(data) {
      heading.textContent = data.title || "For both of us";
      note.textContent = data.note || "";
      list.textContent = "";
      var items = data.items || [];
      items.forEach(function (it) { list.appendChild(pickCard(it)); });
      if (!chipsBox && window.NLWatch) {
        chipsBox = window.NLWatch.chips({
          chips: data.chips, value: value, cls: "tv-pill", focusable: true, groupClass: "tv-chips-inner",
          onChange: function (v) { value = v; load(true); }
        });
        chipsRow.appendChild(chipsBox);
      }
      section.hidden = !items.length && !value;
      list.hidden = !items.length;
    }

    function load(refocus) {
      var mine = ++seq;
      var url = "/api/nl/picks" + (value ? "?max_runtime=" + encodeURIComponent(value) : "");
      NL.fetchJSON(url).then(function (data) {
        if (mine !== seq) { return; }
        var f = document.activeElement;
        var keep = refocus && f && f.getAttribute ? f.getAttribute("data-minutes") : null;
        render(data);
        if (keep) {
          var again = chipsRow.querySelector('[data-minutes="' + keep + '"]');
          if (again) { window.NLFocus.focus(again); }
        }
      }).catch(function () { if (mine === seq) { section.hidden = true; } });
    }
    load(false);
  }

  /* --------------------------------------------- watchlist page (M6) */

  function initWatchlist() {
    var chipsRow = document.getElementById("tv-wl-chips");
    var listBox = document.getElementById("tv-wl-list");
    var chipsBox = null;
    var value = window.NLWatch ? window.NLWatch.savedRuntime() : null;
    var seq = 0;
    window.NLFocus.remember(false);

    function fact(c) {
      var bits = [];
      if (c.both_yes) { bits.push("You both said yes"); }
      bits.push(c.ready ? "Ready" : "Not in library yet");
      if (c.personal) { bits.push("Just me"); }
      return bits.join(" · ");
    }

    function item(c) {
      var li = el("li", "tv-wl-item");
      var info = el("div", "tv-wl-info");
      info.appendChild(el("h3", "tv-wl-title", c.title));
      info.appendChild(el("p", "tv-meta", [NL.metaLine({ year: c.year, runtime: c.runtime, media_type: c.media_type }), fact(c)].filter(Boolean).join(" · ")));
      li.appendChild(info);
      var acts = el("div", "tv-wl-actions");
      acts.setAttribute("data-nl-row", "wl-" + c.id);
      if (c.ready && c.href) {
        var open = el("a", "tv-btn is-primary", "Open");
        open.href = tvHref(c.href);
        open.setAttribute("data-nl-focus", "");
        open.setAttribute("aria-label", "Open " + c.title);
        acts.appendChild(open);
      }
      (c.interest || []).forEach(function (p) {
        if (!p.mine) {
          acts.appendChild(el("span", "tv-wl-read", p.name + ": " + (p.state ? p.state.charAt(0).toUpperCase() + p.state.slice(1) : "Not yet")));
          return;
        }
        var b = window.NLWatch.cycleButton(c.id, p.person, "tv-btn");
        b.setAttribute("data-nl-focus", "");
        acts.appendChild(b);
      });
      var rm = el("button", "tv-btn", "Remove");
      rm.type = "button";
      rm.setAttribute("data-nl-focus", "");
      rm.setAttribute("aria-label", "Remove " + c.title + " from the watchlist");
      rm.addEventListener("click", function () {
        var next = li.nextElementSibling || li.previousElementSibling;
        window.NLWatch.remove(c.id, c.title).then(function () {
          li.remove();
          var target = next && next.querySelector("[data-nl-focus]");
          if (target) { window.NLFocus.focus(target); } else { empty(); }
        }).catch(function () {});
      });
      acts.appendChild(rm);
      li.appendChild(acts);
      return li;
    }

    function empty() {
      if (!listBox.children.length) {
        setStatus(value ? "Nothing on the watchlist fits that length." : "Nothing here yet. Open a title and choose + Watchlist.");
        var first = chipsRow.querySelector("[data-nl-focus]");
        if (first) { window.NLFocus.focus(first); }
      }
    }

    function render(data) {
      listBox.textContent = "";
      if (!chipsBox) {
        chipsBox = window.NLWatch.chips({
          chips: data.chips, value: value, cls: "tv-pill", focusable: true, groupClass: "tv-chips-inner",
          onChange: function (v) { value = v; load(); }
        });
        chipsRow.appendChild(chipsBox);
      }
      (data.items || []).forEach(function (c) { listBox.appendChild(item(c)); });
      setStatus("");
      empty();
    }

    function load() {
      var mine = ++seq;
      var f = document.activeElement;
      var keep = f && f.getAttribute ? f.getAttribute("data-minutes") : null;
      var url = "/api/nl/watchlist" + (value ? "?max_runtime=" + encodeURIComponent(value) : "");
      NL.fetchJSON(url).then(function (data) {
        if (mine !== seq) { return; }
        render(data);
        if (keep) {
          var again = chipsRow.querySelector('[data-minutes="' + keep + '"]');
          if (again) { window.NLFocus.focus(again); return; }
        }
        window.NLFocus.start();
      }).catch(function () { setStatus("Could not load the watchlist. Is the app running?"); window.NLFocus.start(); });
    }
    load();
  }

  /* ----------------------------------------------------- choose for us (M9b) */

  function initChoose() {
    window.NLFocus.remember(false);
    window.NLChoose.mount(document.getElementById("tv-choose"), { tv: true, href: tvHref, status: setStatus });
  }

  /* ------------------------------------------------------------------ init */

  if (page === "home") { initHome(); }
  else if (page === "title") { initTitle(); }
  else if (page === "show") { initShow(); }
  else if (page === "search") { initSearch(); }
  else if (page === "who") { initWho(); }
  else if (page === "me") { initMe(); }
  else if (page === "watchlist") { initWatchlist(); }
  else if (page === "choose") { initChoose(); }
})();
