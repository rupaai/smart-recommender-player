/* Northern Lights "New & popular": new episodes of your shows and popular shows, with a Download button.
 * Shared by the web page (/nl/new), the TV page (/nl/tv/new) and the laptop (/player/new).
 *
 *   NLNew.mount(host, {tv: bool, href: fn(path) -> path, status: fn(text), downloads: url})
 *   NLNew.homeRow(section, list, build, {root, limit})   the "New episodes" row on home
 *
 * Everything is worked out on the server (GET /api/nl/new-episodes): this file only draws the cards and sends
 * the button press (POST /api/nl/new-episodes/download). Copies come with a copy_id; the server keeps the links.
 * All text goes in through textContent (NL.el). */
(function (global) {
  "use strict";
  var NL = global.NL;
  var el = NL.el;

  function post(url, payload) {
    return NL.fetchJSON(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
  }

  function offerPayload(card, copyId) {
    var o = card.offer || {};
    var p = { tmdb_id: card.tmdb_id, season: o.season };
    if (o.episode) { p.episode = o.episode; }
    if (copyId) { p.copy_id = copyId; }
    return p;
  }

  function copiesUrl(card) {
    var o = card.offer || {};
    return "/api/nl/new-episodes/copies?tmdb_id=" + encodeURIComponent(card.tmdb_id) + "&season=" + encodeURIComponent(o.season) +
      (o.episode ? "&episode=" + encodeURIComponent(o.episode) : "");
  }

  function copyLine(c) {
    return [c.quality, c.size, c.seeds + " sharing"].filter(Boolean).join(" · ");
  }

  function mount(host, opts) {
    opts = opts || {};
    var tv = !!opts.tv;
    var p = tv ? "tv" : "nl";
    var href = opts.href || function (x) { return x; };
    var setStatus = opts.status || NL.setStatus;
    var downloadsHref = opts.downloads || "/nl/downloads";
    var announce = el("p", tv ? "tv-visually-hidden" : "nl-visually-hidden");
    announce.setAttribute("role", "status");
    announce.setAttribute("aria-live", "polite");
    host.appendChild(announce);

    function focusable(node) { if (tv) { node.setAttribute("data-nl-focus", ""); } return node; }
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

    function started(card, getBtn, msg, res) {
      getBtn.disabled = true;
      getBtn.textContent = "Downloading";
      msg.className = p + "-new-msg is-ok";
      msg.textContent = "Started: " + (res.release || card.title) + ". ";
      msg.appendChild(link("See Downloads", "See downloads", downloadsHref, p + "-new-msg-link"));
      announce.textContent = "Downloading " + card.title + ".";
    }

    function failed(btn, label, msg, e) {
      btn.disabled = false;
      btn.textContent = label;
      msg.className = p + "-new-msg is-bad";
      msg.textContent = e.message || "That didn't work.";
      announce.textContent = msg.textContent;
    }

    function build(card, section) {
      var li = el("li", p + "-new-item");
      li.id = "show-" + card.tmdb_id;
      var art = el("div", p + "-new-art");
      NL.fillImage(art, [card.poster_url], card.title);
      li.appendChild(art);

      var main = el("div", p + "-new-main");
      main.appendChild(el(tv ? "h3" : "h3", p + "-new-title", card.title));
      var o = card.offer || {};
      main.appendChild(el("p", p + "-new-line", o.status_line || ""));
      var meta = [card.why, card.network, card.rating ? "TMDB " + card.rating : ""].filter(Boolean).join(" · ");
      main.appendChild(el("p", p + "-new-meta", meta));
      if (o.latest && o.latest.name && o.kind === "episode") {
        main.appendChild(el("p", p + "-new-ep", "Latest: " + o.latest.name));
      }
      if (!tv && card.overview) { main.appendChild(el("p", p + "-new-overview", card.overview)); }
      var msg = el("p", p + "-new-msg", "");
      msg.setAttribute("role", "alert");
      main.appendChild(msg);

      var acts = el("div", p + "-new-actions");
      if (tv) { acts.setAttribute("data-nl-row", "new-" + section + "-" + card.tmdb_id); }
      var label = o.label || "Download";
      var getBtn = button(card.downloading ? "Downloading" : label, label + " of " + card.title,
        tv ? "tv-btn is-primary" : "nl-btn-main");
      getBtn.disabled = !!card.downloading;
      getBtn.addEventListener("click", function () {
        getBtn.disabled = true;
        getBtn.textContent = "Finding a copy…";
        msg.textContent = "";
        post("/api/nl/new-episodes/download", offerPayload(card)).then(function (res) {
          started(card, getBtn, msg, res);
        }).catch(function (e) { failed(getBtn, label, msg, e); });
      });
      acts.appendChild(getBtn);

      var copiesBox = el("ul", p + "-new-copies");
      copiesBox.hidden = true;
      if (tv) { copiesBox.setAttribute("data-nl-row", "copies-" + section + "-" + card.tmdb_id); }
      var other = button("Other copies", "Choose another copy of " + card.title, tv ? "tv-btn" : "nl-btn-quiet");
      other.addEventListener("click", function () {
        if (!copiesBox.hidden) { copiesBox.hidden = true; other.textContent = "Other copies"; return; }
        other.disabled = true;
        other.textContent = "Looking…";
        NL.fetchJSON(copiesUrl(card)).then(function (data) {
          other.disabled = false;
          other.textContent = "Hide copies";
          copiesBox.textContent = "";
          var copies = data.copies || [];
          if (!copies.length) {
            copiesBox.appendChild(el("li", p + "-new-copy-empty", "No copies found yet. New episodes often show up a few hours after they air."));
          }
          copies.forEach(function (c) {
            var row = el("li", p + "-new-copy");
            var pick = button("Get", "Download " + c.name + ", " + copyLine(c), tv ? "tv-btn" : "nl-btn-quiet");
            pick.addEventListener("click", function () {
              pick.disabled = true;
              post("/api/nl/new-episodes/download", offerPayload(card, c.copy_id)).then(function (res) {
                started(card, getBtn, msg, res);
                copiesBox.hidden = true;
                other.textContent = "Other copies";
                if (tv && global.NLFocus) { global.NLFocus.focus(other); }
              }).catch(function (e) { failed(pick, "Get", msg, e); });
            });
            row.appendChild(pick);
            var text = el("div", p + "-new-copy-text");
            text.appendChild(el("div", p + "-new-copy-name", c.name));
            text.appendChild(el("div", p + "-new-copy-meta", copyLine(c)));
            row.appendChild(text);
            copiesBox.appendChild(row);
          });
          copiesBox.hidden = false;
          if (tv && global.NLFocus) {
            var first = copiesBox.querySelector("[data-nl-focus]");
            if (first) { global.NLFocus.focus(first); }
          }
        }).catch(function (e) {
          other.disabled = false;
          other.textContent = "Other copies";
          msg.className = p + "-new-msg is-bad";
          msg.textContent = e.message || "Could not look for copies.";
        });
      });
      acts.appendChild(other);
      if (card.href) { acts.appendChild(link("Open show", "Open " + card.title, card.href, tv ? "tv-btn" : "nl-btn-quiet")); }
      main.appendChild(acts);
      main.appendChild(copiesBox);
      li.appendChild(main);
      return li;
    }

    function section(id, heading, sub, cards, emptyText) {
      var box = el("section", p + "-new-section");
      box.setAttribute("aria-labelledby", id + "-h");
      var h = el("h2", p + "-new-h", heading);
      h.id = id + "-h";
      box.appendChild(h);
      if (sub) { box.appendChild(el("p", p + "-new-sub", sub)); }
      if (!cards.length) {
        box.appendChild(el("p", p + "-new-empty", emptyText));
      } else {
        var list = el("ul", p + "-new-list");
        cards.forEach(function (c) { list.appendChild(build(c, id)); });
        box.appendChild(list);
      }
      host.appendChild(box);
    }

    NL.fetchJSON("/api/nl/new-episodes").then(function (data) {
      if (!data.enabled) {
        setStatus(data.note || "New & popular is turned off on the server.");
        return;
      }
      setStatus(data.tmdb === false ? "TMDB isn't answering right now, so this list may be out of date." : "");
      section("new-yours", "New episodes of your shows", "Shows you have, watched or saved, with an episode you don't have yet.",
        data.yours || [], "Nothing new from your shows in the last few weeks.");
      section("new-popular", "Popular right now", "Trending this week, still airing, well rated and your kind of show.",
        data.popular || [], "No popular shows match right now.");
      if (tv && global.NLFocus) {
        var target = (location.hash && document.getElementById(location.hash.slice(1))) || null;
        var btn = target && target.querySelector("[data-nl-focus]");
        if (btn) { global.NLFocus.focus(btn); } else { global.NLFocus.start(); }
      } else if (location.hash) {
        var spot = document.getElementById(location.hash.slice(1));
        if (spot) { spot.scrollIntoView(); }
      }
    }).catch(function () {
      setStatus("Could not load new episodes. Is the app running?");
    });
  }

  /* The "New episodes" row on the home pages: posters that open the New & popular page at that show. Your shows
   * first, then popular ones, up to `limit`. Hidden when there is nothing. Returns the request's promise. */
  function homeRow(sectionEl, listEl, build, opts) {
    opts = opts || {};
    var root = opts.root || "/nl/";
    var limit = opts.limit || 12;
    return NL.fetchJSON("/api/nl/new-episodes").then(function (data) {
      var cards = (data.yours || []).concat(data.popular || []).slice(0, limit);
      if (!data.enabled || !cards.length) { sectionEl.hidden = true; return false; }
      cards.forEach(function (c) {
        var o = c.offer || {};
        var badge = c.downloading ? "Downloading" : (o.kind === "season" ? "Season " + o.season
          : "S" + o.latest.season + " · E" + o.latest.episode + " new");
        listEl.appendChild(build({
          id: null, media_type: "tv", title: c.title, year: c.year, poster_url: c.poster_url,
          badge: badge, href: root + "new#show-" + c.tmdb_id
        }));
      });
      sectionEl.hidden = false;
      return true;
    }).catch(function () { sectionEl.hidden = true; return false; });
  }

  global.NLNew = { mount: mount, homeRow: homeRow, offerPayload: offerPayload, copiesUrl: copiesUrl };
})(window);
