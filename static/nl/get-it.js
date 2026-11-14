/* Northern Lights "Get it": search a film or show, see every season of a show on one page, and download the
 * best pick or a copy you choose. Shared by the web page (/nl/get), the TV page (/nl/tv/get) and the laptop
 * (/player/get).
 *
 *   NLGet.mount(host, {tv: bool, href: fn(path) -> path, status: fn(text), downloads: url, self: url})
 *
 * The page address says what to show: ?q=text (search), ?kind=tv&tmdb=1234 (a title), ?item=56 (a catalogue
 * title: "Find a copy" elsewhere links here). Everything is worked out on the server (services/nl_get_it.py);
 * copies and downloads go through the New & popular calls, so the best pick is the same everywhere. Copies come
 * with a copy_id; the links to the copies stay on the server. All text goes in through textContent (NL.el). */
(function (global) {
  "use strict";
  var NL = global.NL;
  var el = NL.el;

  function post(url, payload) {
    return NL.fetchJSON(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
  }

  function copyLine(c) {
    return [c.quality, c.is_pack ? "season pack" : "", c.size,
      c.seeds + (c.seeds_ok === false ? " sharing, few for this size" : " sharing")].filter(Boolean).join(" · ");
  }

  function copiesUrl(kind, tmdbId, season, episode) {
    var u = "/api/nl/new-episodes/copies?tmdb_id=" + encodeURIComponent(tmdbId);
    if (kind === "movie") { return u + "&kind=movie"; }
    return u + "&season=" + encodeURIComponent(season) + (episode ? "&episode=" + encodeURIComponent(episode) : "");
  }

  function payload(kind, tmdbId, season, episode, copyId) {
    var p = { tmdb_id: tmdbId, kind: kind };
    if (season) { p.season = season; }
    if (episode) { p.episode = episode; }
    if (copyId) { p.copy_id = copyId; }
    return p;
  }

  function titleQuery(kind, tmdbId) { return "?kind=" + encodeURIComponent(kind) + "&tmdb=" + encodeURIComponent(tmdbId); }

  function metaLine(t) {
    return [t.kind === "tv" ? "Show" : "Film", t.year, t.rating ? "TMDB " + t.rating : ""].filter(Boolean).join(" · ");
  }

  function mount(host, opts) {
    opts = opts || {};
    var tv = !!opts.tv;
    var p = tv ? "tv" : "nl";
    var href = opts.href || function (x) { return x; };
    var setStatus = opts.status || NL.setStatus;
    var downloadsHref = opts.downloads || "/nl/downloads";
    var selfHref = opts.self || global.location.pathname;
    var announce = el("p", tv ? "tv-visually-hidden" : "nl-visually-hidden");
    announce.setAttribute("role", "status");
    announce.setAttribute("aria-live", "polite");
    host.appendChild(announce);
    var body = el("div", p + "-get-body");
    host.appendChild(body);

    function focusable(node) { if (tv) { node.setAttribute("data-nl-focus", ""); } return node; }
    function row(node, name) { if (tv) { node.setAttribute("data-nl-row", name); } return node; }
    function button(label, aria, cls) {
      var b = focusable(el("button", cls, label));
      b.type = "button";
      b.setAttribute("aria-label", aria);
      return b;
    }
    function link(label, aria, to, cls) {
      var a = focusable(el("a", cls, label));
      a.href = href(to);
      a.setAttribute("aria-label", aria);
      return a;
    }
    function mainBtn(label, aria) { return button(label, aria, tv ? "tv-btn is-primary" : "nl-btn-main"); }
    function quietBtn(label, aria) { return button(label, aria, tv ? "tv-btn" : "nl-btn-quiet"); }
    function focusFirst(node) {
      if (!tv || !global.NLFocus) { return; }
      var first = node.querySelector("[data-nl-focus]");
      if (first) { global.NLFocus.focus(first); }
    }
    function message() {
      var m = el("p", p + "-get-msg", "");
      m.setAttribute("role", "alert");
      return m;
    }
    function started(btn, msg, res, what) {
      btn.disabled = true;
      btn.textContent = "Downloading";
      msg.className = p + "-get-msg is-ok";
      msg.textContent = "Started: " + (res.release || what) + ". ";
      msg.appendChild(link("See Downloads", "See downloads", downloadsHref, p + "-get-msg-link"));
      announce.textContent = "Downloading " + what + ".";
    }
    function failed(btn, label, msg, e) {
      btn.disabled = false;
      btn.textContent = label;
      msg.className = p + "-get-msg is-bad";
      msg.textContent = e.message || "That didn't work.";
      announce.textContent = msg.textContent;
    }
    function getButton(label, aria, data, msg, what, primary) {
      var b = primary ? mainBtn(label, aria) : quietBtn(label, aria);
      b.addEventListener("click", function () {
        b.disabled = true;
        b.textContent = "Finding a copy…";
        msg.textContent = "";
        post("/api/nl/new-episodes/download", data).then(function (res) { started(b, msg, res, what); })
          .catch(function (e) { failed(b, label, msg, e); });
      });
      return b;
    }

    /* The copies of a film or a season: best pick first, each with its own Get. */
    function copiesList(kind, tmdbId, season, episode, what, msg, rowName) {
      var box = row(el("ul", p + "-get-copies"), rowName);
      box.appendChild(el("li", p + "-get-empty", "Looking for copies…"));
      NL.fetchJSON(copiesUrl(kind, tmdbId, season, episode)).then(function (data) {
        box.textContent = "";
        var copies = data.copies || [];
        if (!copies.length) {
          box.appendChild(el("li", p + "-get-empty", kind === "movie"
            ? "No copies found yet. Films often show up once they're out to stream or buy."
            : "No copies found yet. Try single episodes, or check again later."));
          return;
        }
        copies.forEach(function (c) {
          var li = row(el("li", p + "-get-copy" + (c.best_pick ? " is-best" : "") + (c.seeds_ok === false ? " is-thin" : "")),
            rowName + "-" + c.copy_id);
          li.appendChild(getButton(c.best_pick ? "Get best pick" : "Get", "Download " + c.name + ", " + copyLine(c),
            payload(kind, tmdbId, season, episode, c.copy_id), msg, what, c.best_pick));
          var text = el("div", p + "-get-copy-text");
          if (c.best_pick) { text.appendChild(el("span", p + "-get-badge", "Best pick")); }
          text.appendChild(el("div", p + "-get-copy-name", c.name));
          text.appendChild(el("div", p + "-get-copy-meta", copyLine(c)));
          li.appendChild(text);
          box.appendChild(li);
        });
        focusFirst(box);
      }).catch(function (e) {
        box.textContent = "";
        box.appendChild(el("li", p + "-get-empty", e.message || "Could not look for copies."));
      });
      return box;
    }

    /* ---------- search */
    function showSearch(query, kind) {
      body.textContent = "";
      var form = el("form", p + "-get-search");
      form.setAttribute("role", "search");
      var label = el("label", tv ? "tv-visually-hidden" : "nl-visually-hidden", "Film or show to download");
      label.setAttribute("for", "get-q");
      var input = focusable(el("input", p + "-get-input"));
      input.id = "get-q";
      input.type = "search";
      input.placeholder = "Film or show, e.g. Scrubs";
      input.autocomplete = "off";
      input.value = query || "";
      var go = button("Search", "Search", tv ? "tv-btn is-primary" : "nl-btn-main");
      go.type = "submit";
      var formRow = row(el("div", p + "-get-search-row"), "get-search");
      formRow.appendChild(input);
      formRow.appendChild(go);
      form.appendChild(label);
      form.appendChild(formRow);
      var chips = row(el("div", p + "-get-kinds"), "get-kinds");
      var kinds = [["all", "Both"], ["movie", "Films"], ["tv", "Shows"]];
      kind = kind || "all";
      kinds.forEach(function (k) {
        var c = button(k[1], "Only " + k[1].toLowerCase(), tv ? "tv-pill" : "nl-chip");
        c.setAttribute("aria-pressed", String(k[0] === kind));
        c.addEventListener("click", function () { kind = k[0]; run(); });
        chips.appendChild(c);
      });
      form.appendChild(chips);
      body.appendChild(form);
      var list = el("ul", p + "-get-results");
      body.appendChild(list);

      function run() {
        var q = input.value.trim();
        Array.prototype.forEach.call(chips.children, function (c, i) { c.setAttribute("aria-pressed", String(kinds[i][0] === kind)); });
        if (q.length < 2) { list.textContent = ""; return; }
        try { global.history.replaceState(null, "", selfHref + "?q=" + encodeURIComponent(q) + (kind !== "all" ? "&type=" + kind : "")); } catch (e) { /* not needed */ }
        list.textContent = "";
        list.appendChild(el("li", p + "-get-empty", "Searching…"));
        NL.fetchJSON("/api/nl/get/search?q=" + encodeURIComponent(q) + "&kind=" + kind).then(function (data) {
          list.textContent = "";
          var rows = data.results || [];
          if (!rows.length) { list.appendChild(el("li", p + "-get-empty", "Nothing found for “" + q + "”. Try a shorter title.")); }
          rows.forEach(function (t) {
            var li = row(el("li", p + "-get-result"), "res-" + t.kind + "-" + t.tmdb_id);
            var a = link("", "Open " + t.title + (t.year ? " (" + t.year + ")" : "") + ", " + t.have_label,
              selfHref + titleQuery(t.kind, t.tmdb_id), p + "-get-result-link");
            var art = el("div", p + "-get-art");
            NL.fillImage(art, [t.poster_url], t.title);
            a.appendChild(art);
            var text = el("div", p + "-get-result-text");
            text.appendChild(el("div", p + "-get-result-title", t.title + (t.year ? " (" + t.year + ")" : "")));
            text.appendChild(el("div", p + "-get-result-meta", metaLine(t)));
            if (t.same_name) { text.appendChild(el("div", p + "-get-result-meta", "Another title has this name: check the year.")); }
            a.appendChild(text);
            a.appendChild(el("span", p + "-get-pill" + (t.in_library ? " is-here" : ""), t.have_label));
            li.appendChild(a);
            list.appendChild(li);
          });
        }).catch(function (e) {
          list.textContent = "";
          list.appendChild(el("li", p + "-get-empty", e.message || "Could not search."));
        });
      }
      form.addEventListener("submit", function (ev) { ev.preventDefault(); run(); });
      setStatus("");
      if (query) { run(); }
      if (tv && global.NLFocus) { global.NLFocus.start(); }
      if (!tv) { input.focus(); }
    }

    /* ---------- a title */
    function head(t) {
      var top = el("div", p + "-get-head");
      var art = el("div", p + "-get-head-art");
      NL.fillImage(art, [t.poster_url], t.title);
      top.appendChild(art);
      var text = el("div", p + "-get-head-text");
      text.appendChild(el("h1", p + "-get-title", t.title + (t.year ? " (" + t.year + ")" : "")));
      text.appendChild(el("p", p + "-get-meta", [metaLine(t), t.network].filter(Boolean).join(" · ")));
      if (t.kind === "tv") {
        text.appendChild(el("p", p + "-get-meta", t.season_count + " season" + (t.season_count === 1 ? "" : "s") +
          (t.complete_seasons ? " · " + t.complete_seasons + " fully on the server" : "")));
      }
      if (!tv && t.overview) { text.appendChild(el("p", p + "-get-overview", t.overview)); }
      var acts = row(el("div", p + "-get-actions"), "get-head");
      acts.appendChild(link("Back to search", "Back to search", selfHref, tv ? "tv-btn" : "nl-btn-quiet"));
      if (t.href) { acts.appendChild(link(t.kind === "tv" ? "Open show" : "Open film", "Open " + t.title, t.href, tv ? "tv-btn" : "nl-btn-quiet")); }
      text.appendChild(acts);
      top.appendChild(text);
      return top;
    }

    function showMovie(t) {
      body.appendChild(head(t));
      var msg = message();
      body.appendChild(msg);
      if (t.in_library) { body.appendChild(el("p", p + "-get-note", "This film is already on the server. You can still get another copy.")); }
      body.appendChild(el("h2", p + "-get-h2", "Copies"));
      body.appendChild(copiesList("movie", t.tmdb_id, null, null, t.title, msg, "movie-copies"));
    }

    function seasonRow(t, s) {
      var li = el("li", p + "-get-season is-" + s.state);
      li.id = "season-" + s.season;
      var top = el("div", p + "-get-season-top");
      top.appendChild(el("h3", p + "-get-season-title", s.name || "Season " + s.season));
      top.appendChild(el("span", p + "-get-pill" + (s.state === "complete" ? " is-here" : s.state === "partial" ? " is-part" : ""), s.line));
      li.appendChild(top);
      var msg = message();
      var acts = row(el("div", p + "-get-actions"), "season-" + s.season);
      var what = t.title + " season " + s.season;
      var extra = el("div", p + "-get-season-extra");
      if (s.can_get_season) {
        acts.appendChild(getButton(s.state === "partial" ? "Get the whole season" : "Get season " + s.season,
          "Download the best copy of season " + s.season + " of " + t.title, payload("tv", t.tmdb_id, s.season), msg, what, true));
        var choose = quietBtn("Choose a copy", "Choose a copy of season " + s.season + " of " + t.title);
        choose.addEventListener("click", function () {
          extra.textContent = "";
          extra.appendChild(copiesList("tv", t.tmdb_id, s.season, null, what, msg, "copies-" + s.season));
        });
        acts.appendChild(choose);
      }
      if (s.state !== "upcoming") {
        var eps = quietBtn("Episodes", "Episodes of season " + s.season + " of " + t.title);
        eps.addEventListener("click", function () { showEpisodes(t, s, extra, msg); });
        acts.appendChild(eps);
      }
      if (acts.children.length) { li.appendChild(acts); }
      li.appendChild(msg);
      li.appendChild(extra);
      return li;
    }

    function showEpisodes(t, s, box, msg) {
      box.textContent = "";
      var list = el("ul", p + "-get-episodes");
      list.appendChild(el("li", p + "-get-empty", "Loading season " + s.season + "…"));
      box.appendChild(list);
      NL.fetchJSON("/api/nl/new-episodes/show/" + encodeURIComponent(t.tmdb_id) + "/season/" + encodeURIComponent(s.season)).then(function (data) {
        list.textContent = "";
        (data.episodes || []).forEach(function (e) {
          var li = row(el("li", p + "-get-ep"), "ep-" + e.code);
          if (e.have) { li.appendChild(el("span", p + "-get-ep-state", "On server")); }
          else if (e.aired) {
            li.appendChild(getButton("Get", "Download " + e.code + " of " + t.title, payload("tv", t.tmdb_id, e.season, e.episode),
              msg, t.title + " " + e.code, false));
          } else { li.appendChild(el("span", p + "-get-ep-state", e.when ? "Out " + e.when : "Not out yet")); }
          li.appendChild(el("span", p + "-get-ep-name", e.code + (e.name ? " · " + e.name : "")));
          list.appendChild(li);
        });
        focusFirst(list);
      }).catch(function (e) {
        list.textContent = "";
        list.appendChild(el("li", p + "-get-empty", e.message || "Could not load that season."));
      });
    }

    function showShow(t) {
      body.appendChild(head(t));
      body.appendChild(el("h2", p + "-get-h2", "Seasons"));
      var list = el("ul", p + "-get-seasons");
      (t.seasons || []).forEach(function (s) { list.appendChild(seasonRow(t, s)); });
      if (!(t.seasons || []).length) { list.appendChild(el("li", p + "-get-empty", "TMDB lists no seasons for this show yet.")); }
      body.appendChild(list);
    }

    function showTitle(kind, tmdbId) {
      body.textContent = "";
      setStatus("Loading…");
      NL.fetchJSON("/api/nl/get/" + encodeURIComponent(kind) + "/" + encodeURIComponent(tmdbId)).then(function (t) {
        setStatus("");
        if (kind === "movie") { showMovie(t); } else { showShow(t); }
        if (tv && global.NLFocus) { global.NLFocus.start(); }
      }).catch(function (e) {
        setStatus(e.message || "Could not load that title.");
        body.appendChild(link("Back to search", "Back to search", selfHref, tv ? "tv-btn" : "nl-btn-quiet"));
        if (tv && global.NLFocus) { global.NLFocus.start(); }
      });
    }

    var params = new URLSearchParams(global.location.search);
    if (params.get("tmdb") && params.get("kind")) {
      showTitle(params.get("kind") === "movie" ? "movie" : "tv", params.get("tmdb"));
    } else if (params.get("item")) {
      NL.fetchJSON("/api/nl/get/item/" + encodeURIComponent(params.get("item"))).then(function (x) {
        if (x.tmdb_id) { showTitle(x.kind, x.tmdb_id); } else { showSearch(x.query, x.kind); }
      }).catch(function () { showSearch("", "all"); });
    } else {
      showSearch(params.get("q") || "", params.get("type") || "all");
    }
  }

  global.NLGet = { mount: mount, copyLine: copyLine, copiesUrl: copiesUrl, payload: payload };
})(window);
