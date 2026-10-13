/* Northern Lights browse polish (M7b): "Hide from home", the Unwatched only switch, and the details page extras
 * (Trailer, collapsible Cast & crew and Tech details, "More like this").
 *
 * The same builders serve the phone/desktop pages and the TV pages (`opts.tv`): on the TV everything a person can
 * press is a data-nl-focus button in a row the page already navigates, nothing opens a new page or window, and the
 * Trailer is a line of text (the TV's WebView only keeps pages of this app).
 *
 * Everything comes from our own JSON (/api/nl/...); no Jellyfin, TMDB or YouTube key is ever here. Text goes in
 * through textContent only. Nothing here may break a page: every request failure just leaves that part out. */
(function () {
  "use strict";
  var NL = window.NL;
  var el = NL.el;

  function lsGet(k) { try { return window.localStorage.getItem(k); } catch (e) { return null; } }
  function lsSet(k, v) { try { window.localStorage.setItem(k, String(v)); } catch (e) { /* private mode */ } }

  function putJSON(url, body) {
    return NL.fetchJSON(url, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  }

  /* ------------------------------------------------------- pure helpers */

  function hideLabel(hidden) { return hidden ? "Show on home again" : "Hide from home"; }

  function peopleSummary(extras) {
    var cast = (extras && extras.cast || []).length, crew = (extras && extras.crew || []).length;
    var bits = [];
    if (cast) { bits.push(cast + " cast"); }
    if (crew) { bits.push(crew + " crew"); }
    return bits.join(", ");
  }

  /* A trailer is only ever linked when the server sent a YouTube address of the exact expected shape. */
  function trailerHref(trailer) {
    var url = trailer && trailer.url;
    return typeof url === "string" && /^https:\/\/www\.youtube\.com\/watch\?v=[A-Za-z0-9_-]{6,20}$/.test(url) ? url : null;
  }

  /* ---------------------------------------------------------- hide from home */

  var toastTimer = null;
  function toast(message, undo) {
    var t = document.getElementById("nl-hide-toast");
    if (!t) {
      t = el("div", "nl-toast nl-hide-toast");
      t.id = "nl-hide-toast";
      t.setAttribute("role", "status");
      document.body.appendChild(t);
    }
    t.textContent = message;
    if (undo) {
      var b = el("button", "nl-toast-undo", "Undo");
      b.type = "button";
      b.addEventListener("click", function () { t.hidden = true; undo(); });
      t.appendChild(document.createTextNode(" "));
      t.appendChild(b);
    }
    t.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { t.hidden = true; }, 8000);
  }

  function tidyRows() {
    Array.prototype.forEach.call(document.querySelectorAll(".nl-row"), function (section) {
      var list = section.querySelector(".nl-scroller");
      if (list && !list.children.length) { section.hidden = true; }
    });
  }

  /* The small button on a home card. Cards of the same title in other rows go too. */
  function hideButton(item, li) {
    var b = el("button", "nl-hide-icon", "✕");
    b.type = "button";
    b.title = "Hide from home";
    b.setAttribute("aria-label", "Hide from home: " + item.title);
    li.setAttribute("data-nl-card", String(item.id));
    b.addEventListener("click", function (e) {
      e.preventDefault();
      e.stopPropagation();
      b.disabled = true;
      putJSON("/api/nl/hidden/" + encodeURIComponent(item.id), { hidden: true }).then(function () {
        Array.prototype.forEach.call(document.querySelectorAll('[data-nl-card="' + String(item.id) + '"]'), function (n) { n.remove(); });
        tidyRows();
        toast(item.title + " is hidden from home.", function () {
          putJSON("/api/nl/hidden/" + encodeURIComponent(item.id), { hidden: false }).then(function () { window.location.reload(); }).catch(function () {});
        });
      }).catch(function () { b.disabled = false; toast("Could not hide that. Try again."); });
    });
    return b;
  }

  /* ------------------------------------------------------- unwatched switch */

  /* opts: key (storage key), onChange(bool), tv. Returns the button; `button.nlOn` is the current value. */
  function unwatchedSwitch(opts) {
    var b = el("button", opts.tv ? "tv-pill" : "nl-chip", "Unwatched only");
    b.type = "button";
    b.nlOn = lsGet(opts.key) === "1";
    if (opts.tv) { b.setAttribute("data-nl-focus", ""); }
    function paint() { b.setAttribute("aria-pressed", String(b.nlOn)); }
    paint();
    b.addEventListener("click", function () {
      b.nlOn = !b.nlOn;
      lsSet(opts.key, b.nlOn ? "1" : "0");
      paint();
      opts.onChange(b.nlOn);
    });
    return b;
  }

  /* ----------------------------------------------------------- folds, lists */

  /* A heading button that opens and closes a block. Open state is remembered per kind of block. */
  function fold(label, summary, body, opts) {
    opts = opts || {};
    var tv = !!opts.tv;
    var wrap = el("div", "nl-fold");
    var head = el("button", tv ? "tv-btn nl-fold-head" : "nl-fold-head");
    head.type = "button";
    head.appendChild(el("span", "nl-fold-title", label));
    if (summary) { head.appendChild(el("span", "nl-fold-sum", summary)); }
    head.appendChild(el("span", "nl-fold-caret", ""));
    if (tv) { head.setAttribute("data-nl-focus", ""); }
    var panel = el("div", "nl-fold-body");
    panel.appendChild(body);
    var key = opts.key ? "nl.fold." + opts.key : null;
    var open = key ? lsGet(key) === "1" : false;
    function paint() {
      head.setAttribute("aria-expanded", String(open));
      panel.hidden = !open;
      wrap.classList.toggle("is-open", open);
    }
    panel.id = "nl-fold-" + String(opts.key || label).replace(/[^a-z0-9]+/gi, "-").toLowerCase();
    head.setAttribute("aria-controls", panel.id);
    paint();
    head.addEventListener("click", function () {
      open = !open;
      if (key) { lsSet(key, open ? "1" : "0"); }
      paint();
    });
    wrap.appendChild(head);
    wrap.appendChild(panel);
    wrap.nlHead = head;
    return wrap;
  }

  function personItem(p) {
    var li = el("li", "nl-person");
    var photo = el("div", "nl-person-photo");
    photo.setAttribute("aria-hidden", "true");
    NL.fillImage(photo, [p.image], p.name.slice(0, 1));
    li.appendChild(photo);
    var text = el("div", "nl-person-text");
    text.appendChild(el("div", "nl-person-name", p.name));
    if (p.role) { text.appendChild(el("div", "nl-meta", p.role)); }
    li.appendChild(text);
    return li;
  }

  function peopleBody(extras) {
    var box = el("div", "nl-people-box");
    if ((extras.cast || []).length) {
      box.appendChild(el("h3", "nl-fold-sub", "Cast"));
      var cast = el("ul", "nl-people");
      extras.cast.forEach(function (p) { cast.appendChild(personItem(p)); });
      box.appendChild(cast);
    }
    if ((extras.crew || []).length) {
      box.appendChild(el("h3", "nl-fold-sub", "Crew"));
      var crew = el("ul", "nl-people");
      extras.crew.forEach(function (p) { crew.appendChild(personItem(p)); });
      box.appendChild(crew);
    }
    return box;
  }

  function techBody(rows) {
    var dl = el("dl", "nl-tech");
    rows.forEach(function (r) {
      dl.appendChild(el("dt", null, r.label));
      dl.appendChild(el("dd", null, r.value));
    });
    return dl;
  }

  /* --------------------------------------------------------- details page */

  /* t: the title JSON. host: where the folds and "More like this" go. actions: the button row (Trailer lands
   * there on the web). opts: {tv, buildCard(item) -> li, focusRow} */
  function titleExtras(t, host, actions, opts) {
    opts = opts || {};
    var tv = !!opts.tv;
    var id = encodeURIComponent(t.id);

    NL.fetchJSON("/api/nl/title/" + id + "/extras").then(function (x) {
      var href = trailerHref(x.trailer);
      if (href && !tv && actions) {
        var a = el("a", "nl-btn-quiet", "Trailer");
        a.href = href;
        a.target = "_blank";
        a.rel = "noopener noreferrer";
        a.setAttribute("aria-label", "Watch the trailer of " + t.title + " (opens YouTube in a new tab)");
        actions.appendChild(a);
      }
      if (href && tv && opts.trailerNote) {
        // The TV keeps pages of this app only, so the trailer is not offered to the remote.
        opts.trailerNote.textContent = "Trailer: not available on the TV. Open this title on your phone to watch it.";
        opts.trailerNote.hidden = false;
      }
      var box = el("div", "nl-folds");
      if (tv) { box.setAttribute("data-nl-row", "extras"); }
      var added = false;
      if ((x.cast || []).length || (x.crew || []).length) {
        box.appendChild(fold("Cast & crew", peopleSummary(x), peopleBody(x), { key: "cast", tv: tv }));
        added = true;
      }
      if ((x.tech || []).length) {
        box.appendChild(fold("Tech details", "", techBody(x.tech), { key: "tech", tv: tv }));
        added = true;
      }
      if (added) { host.insertBefore(box, host.firstChild); }
    }).catch(function () { /* the page is complete without them */ });

    NL.fetchJSON("/api/nl/title/" + id + "/more").then(function (m) {
      var items = (m && m.items) || [];
      if (!items.length) { return; }
      var section = el("section", tv ? "tv-section" : "nl-row nl-more");
      var h = el("h2", null, "More like this");
      if (tv) {
        section.setAttribute("data-nl-section", "");
        section.appendChild(h);
        var ul = el("ul", "tv-row");
        ul.setAttribute("data-nl-row", "more");
        items.forEach(function (it) { ul.appendChild(opts.buildCard(it)); });
        section.appendChild(ul);
      } else {
        var head = el("div", "nl-row-head");
        head.appendChild(h);
        section.appendChild(head);
        var list = el("ul", "nl-scroller nl-scroller-picks");
        items.forEach(function (it) {
          var li = NL.posterCard(it);
          if (it.reason) { li.appendChild(el("p", "nl-reason", it.reason)); }
          list.appendChild(li);
        });
        section.appendChild(list);
      }
      host.appendChild(section);
    }).catch(function () { /* no row */ });
  }

  /* "Hide from home" / "Show on home again" on a details page. Returns the button. */
  function hideToggle(t, opts) {
    opts = opts || {};
    var b = el("button", opts.tv ? "tv-btn" : "nl-btn-quiet");
    b.type = "button";
    if (opts.tv) { b.setAttribute("data-nl-focus", ""); }
    var hidden = !!t.hidden;
    function paint() {
      b.textContent = hideLabel(hidden);
      b.setAttribute("aria-label", hideLabel(hidden) + ": " + t.title);
    }
    paint();
    b.addEventListener("click", function () {
      var want = !hidden;
      b.disabled = true;
      putJSON("/api/nl/hidden/" + encodeURIComponent(t.id), { hidden: want }).then(function (r) {
        hidden = !!r.hidden;
        paint();
        if (opts.onChange) { opts.onChange(hidden); }
      }).catch(function () {
        if (opts.onChange) { opts.onChange(null); }
      }).then(function () {
        b.disabled = false;
        if (opts.tv && window.NLFocus) { window.NLFocus.focus(b); }
      });
    });
    return b;
  }

  window.NLPolish = {
    hideLabel: hideLabel, peopleSummary: peopleSummary, trailerHref: trailerHref,
    hideButton: hideButton, hideToggle: hideToggle, unwatchedSwitch: unwatchedSwitch, fold: fold,
    titleExtras: titleExtras, tidyRows: tidyRows
  };
})();
