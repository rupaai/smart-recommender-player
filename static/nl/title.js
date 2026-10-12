/* Northern Lights details page (M2): backdrop, title, facts, synopsis and Play / Resume. */
(function () {
  "use strict";
  var NL = window.NL;
  var root = document.getElementById("nl-detail");
  var id = root.getAttribute("data-item-id");

  function render(t) {
    document.title = t.title + " - Northern Lights";
    root.textContent = "";

    var hero = NL.el("div", "nl-hero");
    if (t.backdrop) {
      var back = NL.el("div", "nl-backdrop");
      var img = document.createElement("img");
      img.alt = "";
      img.addEventListener("error", function () { back.remove(); });
      img.src = t.backdrop;
      back.appendChild(img);
      hero.appendChild(back);
    }
    var body = NL.el("div", "nl-hero-body");
    var poster = NL.el("div", "nl-poster nl-hero-poster");
    NL.fillImage(poster, [t.poster_url, t.image_fallback], t.title);
    body.appendChild(poster);

    var head = NL.el("div", "nl-head-info");
    var info = NL.el("div", "nl-info");
    head.appendChild(NL.el("h1", "nl-detail-title", t.title));
    var facts = [];
    if (t.year) { facts.push(String(t.year)); }
    if (t.media_type === "tv") {
      var n = (t.seasons || []).length;
      if (n) { facts.push(n + (n === 1 ? " season" : " seasons")); }
      if (t.episode_count) { facts.push(t.episode_count + (t.episode_count === 1 ? " episode" : " episodes")); }
    } else if (t.runtime) {
      facts.push(NL.fmtRuntime(t.runtime));
    }
    head.appendChild(NL.el("p", "nl-facts", facts.join(" · ")));
    if (t.genres && t.genres.length) {
      var gl = NL.el("ul", "nl-genres");
      t.genres.forEach(function (g) { gl.appendChild(NL.el("li", null, g)); });
      head.appendChild(gl);
    }
    if (t.overview) { info.appendChild(NL.el("p", "nl-overview", t.overview)); }

    var actions = NL.el("div", "nl-actions");
    if (t.media_type === "tv") {
      var eps = NL.el("a", "nl-btn-main", "Episodes");
      eps.href = t.show_href;
      actions.appendChild(eps);
      if (t.seasons && t.seasons.length) {
        var go = NL.el("a", "nl-btn-quiet", "Start from S" + t.seasons[0].number + " · E1");
        go.href = t.play_href;
        actions.appendChild(go);
      }
    } else if (t.can_resume) {
      var res = NL.el("a", "nl-btn-main", "Resume from " + NL.fmtClock(t.resume_seconds));
      res.href = t.resume_href;
      actions.appendChild(res);
      var again = NL.el("a", "nl-btn-quiet", "Start over");
      again.href = t.restart_href;
      actions.appendChild(again);
    } else {
      var play = NL.el("a", "nl-btn-main", t.played ? "Watch again" : "Play");
      play.href = t.play_href;
      actions.appendChild(play);
    }
    var subNote = NL.el("p", "nl-meta nl-note");
    subNote.setAttribute("role", "status");
    if (t.media_type !== "tv" && window.NLSubtitles) {
      // D4: fix a missing or out-of-sync subtitle before pressing play (a show's episodes get it in the player).
      var find = NL.el("button", "nl-btn-quiet", "Find subtitles");
      find.type = "button";
      find.addEventListener("click", function () {
        window.NLSubtitles.open({
          container: document.getElementById("nl-subfind"),
          itemId: t.id,
          onDownloaded: function () {
            subNote.textContent = "Subtitles added. Pick them in the player’s Subtitles menu.";
          },
          onClose: function () { find.focus(); }
        });
      });
      actions.appendChild(find);
    }
    info.appendChild(actions);
    info.appendChild(subNote);
    if (t.media_type !== "tv" && t.progress && !t.played) {
      var p = NL.el("div", "nl-detail-progress");
      p.appendChild(NL.progressBar(t.progress));
      p.appendChild(NL.el("span", "nl-meta", t.progress + "% watched"));
      info.appendChild(p);
    }
    body.appendChild(head);
    body.appendChild(info);
    hero.appendChild(body);
    root.appendChild(hero);
    root.hidden = false;
    NL.setStatus("");
  }

  NL.fetchJSON("/api/nl/title/" + encodeURIComponent(id)).then(render).catch(function (err) {
    NL.setStatus(err.status === 404 ? "That title isn’t in the library." : "Could not load this title. Is the app running?");
  });
})();
