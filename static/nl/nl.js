/* Northern Lights browse (M2): helpers every /nl/ page shares, and the search box.
 * Titles and names come from files and the network, so text always goes in through
 * textContent, never innerHTML. */
(function () {
  "use strict";

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) { node.className = className; }
    if (text !== undefined && text !== null) { node.textContent = text; }
    return node;
  }

  function fetchJSON(url, options) {
    return fetch(url, options).then(function (resp) {
      return resp.json().catch(function () { return {}; }).then(function (body) {
        if (!resp.ok) {
          var err = new Error(body.detail || ("status " + resp.status));
          err.status = resp.status;
          throw err;
        }
        return body;
      });
    });
  }

  function fmtRuntime(min) {
    if (!min) { return ""; }
    var h = Math.floor(min / 60), m = min % 60;
    return h ? h + " h" + (m ? " " + m + " min" : "") : m + " min";
  }

  function fmtClock(sec) {
    sec = Math.max(0, Math.floor(sec || 0));
    var h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
    var ss = (s < 10 ? "0" : "") + s;
    return h > 0 ? h + ":" + (m < 10 ? "0" : "") + m + ":" + ss : m + ":" + ss;
  }

  // year, then runtime (movies) or season count (shows)
  function metaLine(item) {
    var bits = [];
    if (item.year) { bits.push(item.year); }
    if (item.media_type === "tv") {
      if (item.seasons) { bits.push(item.seasons + (item.seasons === 1 ? " season" : " seasons")); }
    } else if (item.runtime) {
      bits.push(fmtRuntime(item.runtime));
    }
    return bits.join(" · ");
  }

  /* Puts the first image that loads into `box`; a title-on-paper card when none does. */
  function fillImage(box, sources, fallbackText) {
    var list = sources.filter(Boolean);
    function showText() {
      box.textContent = "";
      box.appendChild(el("div", "nl-fallback", fallbackText));
    }
    function attempt(i) {
      if (i >= list.length) { showText(); return; }
      var img = document.createElement("img");
      img.alt = "";
      img.loading = "lazy";
      img.decoding = "async";
      img.addEventListener("error", function () { img.remove(); attempt(i + 1); });
      img.src = list[i];
      box.appendChild(img);
    }
    attempt(0);
  }

  function progressBar(pct) {
    var bar = el("div", "nl-progress");
    bar.setAttribute("role", "progressbar");
    bar.setAttribute("aria-valuemin", "0");
    bar.setAttribute("aria-valuemax", "100");
    bar.setAttribute("aria-valuenow", String(pct));
    bar.setAttribute("aria-label", pct + " percent watched");
    var fill = el("span");
    fill.style.width = pct + "%";
    bar.appendChild(fill);
    return bar;
  }

  /* Poster card: poster, title, year and runtime, and one badge at most. */
  function posterCard(item) {
    var li = el("li");
    var a = el("a", "nl-card");
    a.href = item.href;
    var poster = el("div", "nl-poster");
    fillImage(poster, [item.poster_url, item.image_fallback], item.title);
    if (item.progress) {
      poster.appendChild(progressBar(item.progress));
    } else if (item.badge) {
      poster.appendChild(el("span", "nl-badge", item.badge));
    }
    a.appendChild(poster);
    a.appendChild(el("div", "nl-name", item.title));
    a.appendChild(el("div", "nl-meta", metaLine(item)));
    var label = item.title + (item.year ? " (" + item.year + ")" : "");
    if (item.progress) { label += ", " + item.progress + " percent watched"; } else if (item.badge) { label += ", " + item.badge; }
    a.setAttribute("aria-label", label);
    li.appendChild(a);
    // M6: "+" for the watchlist, beside the link (a button inside a link is not valid markup).
    if (window.NLWatch && item.id) { li.appendChild(window.NLWatch.iconButton(item.id, item.title)); }
    // M7b: home rows mark their cards hideable; "Hide from home" sits under the "+".
    if (window.NLPolish && item.hideable && item.id) { li.appendChild(window.NLPolish.hideButton(item, li)); }
    return li;
  }

  /* Landscape card for Continue Watching. */
  function wideCard(c) {
    var li = el("li");
    var a = el("a", "nl-card nl-wide");
    a.href = c.href;
    var frame = el("div", "nl-still");
    fillImage(frame, [c.image, c.poster_url], c.title);
    frame.appendChild(progressBar(c.progress));
    a.appendChild(frame);
    a.appendChild(el("div", "nl-name", c.title));
    a.appendChild(el("div", "nl-meta", c.subtitle || ""));
    a.setAttribute("aria-label", "Continue " + c.title + (c.subtitle ? ", " + c.subtitle : "") + ", " + c.progress + " percent watched");
    li.appendChild(a);
    return li;
  }

  function setStatus(text) {
    var s = document.getElementById("nl-status");
    if (s) { s.textContent = text || ""; }
  }

  /* ---------------------------------------------------------------- search */

  var form = document.getElementById("nl-search-form");
  var input = document.getElementById("nl-q");
  var panel = document.getElementById("nl-results");
  var timer = null;
  var seq = 0;

  function closeResults() {
    panel.hidden = true;
    panel.textContent = "";
    input.setAttribute("aria-expanded", "false");
  }

  function resultRow(r) {
    var ready = r.ready && r.href;
    var row = el(ready ? "a" : "div", "nl-result" + (ready ? "" : " is-away"));
    var wrap = null;
    if (ready) { row.href = r.href; }
    var thumb = el("div", "nl-thumb");
    fillImage(thumb, [r.poster_url], r.title.slice(0, 1));
    row.appendChild(thumb);
    var text = el("div", "nl-result-text");
    text.appendChild(el("div", "nl-result-title", r.title));
    var sub = r.kind === "episode" ? r.subtitle : [r.kind === "show" ? "Show" : "Movie", r.year].filter(Boolean).join(" · ");
    text.appendChild(el("div", "nl-meta", sub));
    row.appendChild(text);
    var tag = el("span", "nl-tag " + (r.ready ? "is-ready" : "is-away"), r.label);
    row.appendChild(tag);
    if (!ready && r.download_href) {
      var dl = el("a", "nl-find", "Find a copy");
      dl.href = r.download_href;
      dl.setAttribute("aria-label", "Find a copy of " + r.title + " in the Download tab");
      row.appendChild(dl);
    }
    // M6: add a movie or show to the watchlist from the search results, ready or not.
    if (window.NLWatch && r.kind !== "episode" && r.id) {
      wrap = el("div", "nl-result-wrap");
      wrap.appendChild(row);
      wrap.appendChild(window.NLWatch.iconButton(r.id, r.title));
    }
    return wrap || row;
  }

  function showResults(results) {
    panel.textContent = "";
    if (!results.length) {
      panel.appendChild(el("p", "nl-result-empty", "Nothing matches that."));
    } else {
      results.forEach(function (r) { panel.appendChild(resultRow(r)); });
    }
    panel.hidden = false;
    input.setAttribute("aria-expanded", "true");
  }

  function runSearch() {
    var q = input.value.trim();
    if (q.length < 2) { closeResults(); return; }
    var mine = ++seq;
    fetchJSON("/api/nl/search?q=" + encodeURIComponent(q)).then(function (data) {
      if (mine === seq) { showResults(data.results || []); }
    }).catch(function () {
      if (mine !== seq) { return; }
      panel.textContent = "";
      panel.appendChild(el("p", "nl-result-empty", "Search isn’t available right now."));
      panel.hidden = false;
    });
  }

  if (form && input && panel) {
    input.addEventListener("input", function () {
      clearTimeout(timer);
      timer = setTimeout(runSearch, 220);
    });
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      clearTimeout(timer);
      var first = panel.querySelector("a.nl-result");
      if (!panel.hidden && first && input.value.trim().length >= 2) { window.location.href = first.href; } else { runSearch(); }
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && !panel.hidden) { closeResults(); input.focus(); }
      if (e.key === "/" && document.activeElement !== input && !/INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName)) {
        e.preventDefault();
        input.focus();
      }
    });
    document.addEventListener("pointerdown", function (e) {
      if (!panel.hidden && !panel.contains(e.target) && !form.contains(e.target)) { closeResults(); }
    });
    input.addEventListener("focus", function () { if (input.value.trim().length >= 2 && panel.childNodes.length) { panel.hidden = false; } });
  }

  window.NL = {
    el: el, fetchJSON: fetchJSON, fmtRuntime: fmtRuntime, fmtClock: fmtClock, metaLine: metaLine,
    fillImage: fillImage, progressBar: progressBar, posterCard: posterCard, wideCard: wideCard, setStatus: setStatus
  };
})();
