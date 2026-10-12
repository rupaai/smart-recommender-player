/* Northern Lights show page (M2): season picker, episode cards with watched ticks and
 * progress, a Next up button and the per-show autoplay switch (P12). */
(function () {
  "use strict";
  var NL = window.NL;
  var root = document.getElementById("nl-detail");
  var id = root.getAttribute("data-item-id");
  var show = null;
  var season = null;
  var episodesBox = null;
  var chips = null;

  function lsGet(k) { try { return window.localStorage.getItem(k); } catch (e) { return null; } }
  function lsSet(k, v) { try { window.localStorage.setItem(k, String(v)); } catch (e) { /* private mode */ } }

  function episodeCard(e) {
    var li = NL.el("li");
    var a = NL.el("a", "nl-episode" + (e.played ? " is-played" : ""));
    a.href = e.href;
    var still = NL.el("div", "nl-still");
    NL.fillImage(still, [e.image], e.code || ("E" + (e.episode || "")));
    if (e.progress) { still.appendChild(NL.progressBar(e.progress)); }
    a.appendChild(still);
    var text = NL.el("div", "nl-episode-text");
    var head = NL.el("div", "nl-episode-head");
    head.appendChild(NL.el("span", "nl-episode-num", e.episode != null ? "E" + e.episode : ""));
    head.appendChild(NL.el("span", "nl-name", e.name));
    text.appendChild(head);
    var meta = [];
    if (e.runtime_minutes) { meta.push(NL.fmtRuntime(e.runtime_minutes)); }
    if (e.progress && !e.played) { meta.push(e.progress + "% watched"); }
    text.appendChild(NL.el("div", "nl-meta", meta.join(" · ")));
    if (e.overview) { text.appendChild(NL.el("p", "nl-episode-overview", e.overview)); }
    a.appendChild(text);
    if (e.played) {
      var tick = NL.el("span", "nl-tick", "✓");
      tick.setAttribute("role", "img");
      tick.setAttribute("aria-label", "Watched");
      a.appendChild(tick);
    }
    a.setAttribute("aria-label", (e.code ? e.code + ", " : "") + e.name + (e.played ? ", watched" : "") +
      (e.progress && !e.played ? ", " + e.progress + " percent watched" : ""));
    li.appendChild(a);
    return li;
  }

  function showSeason(number) {
    season = number;
    lsSet("nl.season." + id, number);
    episodesBox.textContent = "";
    var sn = show.seasons.filter(function (s) { return s.number === number; })[0];
    (sn ? sn.episodes : []).forEach(function (e) { episodesBox.appendChild(episodeCard(e)); });
    Array.prototype.forEach.call(chips.querySelectorAll("button"), function (b) {
      b.setAttribute("aria-pressed", String(Number(b.getAttribute("data-season")) === number));
    });
  }

  function autoplaySwitch() {
    var wrap = NL.el("div", "nl-switch");
    var box = document.createElement("button");
    box.type = "button";
    box.className = "nl-switch-btn";
    box.setAttribute("role", "switch");
    box.setAttribute("aria-checked", String(!!show.autoplay));
    box.appendChild(NL.el("span", "nl-switch-knob"));
    var label = NL.el("span", "nl-switch-label", "Autoplay next episode");
    label.id = "nl-autoplay-label";
    box.setAttribute("aria-labelledby", "nl-autoplay-label");
    var note = NL.el("span", "nl-visually-hidden");
    note.setAttribute("role", "status");
    box.addEventListener("click", function () {
      var want = box.getAttribute("aria-checked") !== "true";
      box.setAttribute("aria-checked", String(want));          // optimistic; rolled back on failure
      NL.fetchJSON("/api/nl/show/" + encodeURIComponent(id) + "/prefs", {
        method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ autoplay: want })
      }).then(function (r) {
        show.autoplay = r.autoplay;
        box.setAttribute("aria-checked", String(!!r.autoplay));
        note.textContent = "Autoplay " + (r.autoplay ? "on" : "off");
      }).catch(function () {
        box.setAttribute("aria-checked", String(!want));
        note.textContent = "Couldn’t save that";
      });
    });
    label.addEventListener("click", function () { box.click(); });
    wrap.appendChild(box);
    wrap.appendChild(label);
    wrap.appendChild(note);
    return wrap;
  }

  function render(s) {
    show = s;
    document.title = s.title + " - Northern Lights";
    root.textContent = "";

    var hero = NL.el("div", "nl-hero nl-hero-show");
    if (s.backdrop) {
      var back = NL.el("div", "nl-backdrop");
      var img = document.createElement("img");
      img.alt = "";
      img.addEventListener("error", function () { back.remove(); });
      img.src = s.backdrop;
      back.appendChild(img);
      hero.appendChild(back);
    }
    var body = NL.el("div", "nl-hero-body");
    var poster = NL.el("div", "nl-poster nl-hero-poster");
    NL.fillImage(poster, [s.poster_url], s.title);
    body.appendChild(poster);
    var head = NL.el("div", "nl-head-info");
    var info = NL.el("div", "nl-info");
    head.appendChild(NL.el("h1", "nl-detail-title", s.title));
    var facts = [];
    if (s.year) { facts.push(String(s.year)); }
    facts.push(s.seasons.length + (s.seasons.length === 1 ? " season" : " seasons"));
    facts.push(s.episode_count + (s.episode_count === 1 ? " episode" : " episodes"));
    head.appendChild(NL.el("p", "nl-facts", facts.join(" · ")));
    if (s.genres.length) {
      var gl = NL.el("ul", "nl-genres");
      s.genres.forEach(function (g) { gl.appendChild(NL.el("li", null, g)); });
      head.appendChild(gl);
    }
    if (s.overview) { info.appendChild(NL.el("p", "nl-overview", s.overview)); }

    var actions = NL.el("div", "nl-actions");
    if (s.next_up) {
      var n = NL.el("a", "nl-btn-main", s.next_up.text);
      n.href = s.next_up.href;
      actions.appendChild(n);
    }
    info.appendChild(actions);
    if (s.next_up && s.next_up.name && s.next_up.name.indexOf("Episode ") !== 0) {
      info.appendChild(NL.el("p", "nl-meta nl-next-name", s.next_up.name));
    }
    info.appendChild(autoplaySwitch());
    if (!s.jellyfin) {
      info.appendChild(NL.el("p", "nl-meta nl-note", "Watched ticks and Next up need Jellyfin, which isn’t answering right now."));
    }
    body.appendChild(head);
    body.appendChild(info);
    hero.appendChild(body);
    root.appendChild(hero);

    var section = NL.el("section", "nl-episodes");
    var head = NL.el("div", "nl-row-head");
    head.appendChild(NL.el("h2", null, "Episodes"));
    section.appendChild(head);
    chips = NL.el("div", "nl-chips");
    chips.setAttribute("role", "group");
    chips.setAttribute("aria-label", "Season");
    s.seasons.forEach(function (sn) {
      var b = NL.el("button", "nl-chip", sn.label);
      b.type = "button";
      b.setAttribute("data-season", sn.number);
      b.addEventListener("click", function () { showSeason(sn.number); });
      chips.appendChild(b);
    });
    if (s.seasons.length > 1) { section.appendChild(chips); }
    episodesBox = NL.el("ul", "nl-episode-list");
    section.appendChild(episodesBox);
    root.appendChild(section);

    // Open on the season of the Next up episode, else the last one picked, else the first.
    var start = s.seasons[0] ? s.seasons[0].number : 0;
    var saved = Number(lsGet("nl.season." + id));
    if (s.seasons.some(function (x) { return x.number === saved; }) && !s.next_up) { start = saved; }
    if (s.next_up) { start = s.next_up.season; }
    showSeason(start);
    root.hidden = false;
    NL.setStatus("");
  }

  NL.fetchJSON("/api/nl/show/" + encodeURIComponent(id)).then(render).catch(function (err) {
    NL.setStatus(err.status === 404 ? "That show isn’t in the library." : "Could not load this show. Is the app running?");
  });
})();
