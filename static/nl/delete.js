/* Northern Lights "Delete from server" (services/nl_delete.py). One button on the title and show pages, web and TV.
 *
 *   NLDelete.button({ itemId, title, isShow, season: function () { return n; }, tv, onDone: function (result, scope) {} })
 *
 * The button opens a confirmation panel. It first asks the server what would go (GET /api/nl/delete/<id>) and
 * shows the name and the space it frees. "Keep it" is focused first, so OK pressed by accident deletes nothing.
 * A show offers "This season" and "Whole show". Only the Delete button sends POST with confirm: true.
 * Keys on the TV: left/right/up/down move between the buttons, OK presses, Back closes. Escape closes on the web. */
(function (global) {
  "use strict";

  var NL = global.NL;
  var BACK_CODES = [4, 27, 461, 10009];
  var OK_CODES = [13, 23, 66];

  function el(tag, cls, text) { return NL.el(tag, cls, text); }

  function previewUrl(itemId, season) {
    return "/api/nl/delete/" + encodeURIComponent(itemId) + (season === null || season === undefined ? "" : "?season=" + encodeURIComponent(season));
  }

  function sentence(p) {
    var files = p.episodes ? (p.episodes === 1 ? "1 episode" : p.episodes + " episodes") : "the film";
    return "This deletes " + files + " (" + p.size + ") from the server for everyone. It can’t be undone. " +
      "Your ratings and watch history stay.";
  }

  function open(opts) {
    var tv = !!opts.tv;
    var host = el("div", "nld-host" + (tv ? " nld-tv" : ""));
    var panel = el("div", "nld-panel");
    panel.setAttribute("role", "alertdialog");
    panel.setAttribute("aria-modal", "true");
    var heading = el("h2", "nld-title", "Delete from server?");
    heading.id = "nld-title";
    panel.setAttribute("aria-labelledby", "nld-title");
    var text = el("p", "nld-text", "Checking what would be deleted…");
    text.id = "nld-text";
    panel.setAttribute("aria-describedby", "nld-text");
    var choices = el("div", "nld-choices");
    var buttonsBox = el("div", "nld-buttons");
    panel.appendChild(heading);
    panel.appendChild(text);
    panel.appendChild(choices);
    panel.appendChild(buttonsBox);
    host.appendChild(panel);
    document.body.appendChild(host);

    var previously = document.activeElement;
    var busy = false;
    var closed = false;
    var picked = null;          // the preview being confirmed
    var previews = {};          // "season" / "show" -> preview

    function buttons() { return Array.prototype.slice.call(panel.querySelectorAll("button:not([disabled])")); }

    function btn(label, cls, onClick) {
      var b = el("button", "nld-btn" + (cls ? " " + cls : ""), label);
      b.type = "button";
      b.addEventListener("click", onClick);
      return b;
    }

    function close() {
      if (closed) { return; }
      closed = true;
      global.removeEventListener("keydown", onKey, true);
      global.removeEventListener("keyup", onKeyUp, true);
      host.remove();
      if (opts.onClose) { opts.onClose(); }
      else if (previously && previously.focus) { try { previously.focus(); } catch (e) { /* gone */ } }
    }

    var keep = btn("Keep it", "nld-keep", function () { if (!busy) { close(); } });
    var del = btn("Delete", "nld-danger", function () { if (picked && !busy) { run(); } });
    del.disabled = true;
    buttonsBox.appendChild(keep);
    buttonsBox.appendChild(del);

    function pick(key) {
      picked = previews[key];
      Array.prototype.forEach.call(choices.querySelectorAll("button"), function (b) {
        b.setAttribute("aria-pressed", String(b.getAttribute("data-key") === key));
      });
      heading.textContent = "Delete " + picked.what + "?";
      text.textContent = sentence(picked);
      del.textContent = "Delete " + picked.size;
      del.disabled = false;
    }

    function run() {
      busy = true;
      keep.disabled = true;
      del.disabled = true;
      Array.prototype.forEach.call(choices.querySelectorAll("button"), function (b) { b.disabled = true; });
      text.textContent = "Deleting…";
      NL.fetchJSON("/api/nl/delete/" + encodeURIComponent(opts.itemId), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ season: picked.season, confirm: true })
      }).then(function (r) {
        busy = false;
        var scope = picked.season === null || picked.season === undefined ? "all" : "season";
        heading.textContent = r.errors ? "Partly deleted" : "Deleted";
        text.textContent = "Freed " + r.freed + "." +
          (r.kept ? " " + r.kept + " file" + (r.kept === 1 ? " is" : "s are") + " outside the Movies and Shows folders, so they were kept." : "") +
          (r.errors ? " Some files could not be deleted. Try again later." : "");
        buttonsBox.textContent = "";
        choices.textContent = "";
        var ok = btn("OK", "nld-keep", function () { close(); if (opts.onDone) { opts.onDone(r, scope); } });
        buttonsBox.appendChild(ok);
        ok.focus();
      }).catch(function (err) {
        busy = false;
        keep.disabled = false;
        del.disabled = false;
        text.textContent = err.status === 404 ? "That is already gone from the library." : "Could not delete it. Is the app running? Nothing was deleted.";
        keep.focus();
      });
    }

    function onKey(e) {
      if (e.ctrlKey || e.metaKey || e.altKey) { return; }
      var back = e.key === "Escape" || e.key === "GoBack" || e.key === "BrowserBack" || (tv && BACK_CODES.indexOf(e.keyCode) >= 0);
      if (back) {
        e.preventDefault();
        e.stopImmediatePropagation();
        if (!e.repeat && !busy) { close(); }
        return;
      }
      if (!tv) {
        if (e.key === "Tab") {           // keep Tab inside the panel
          var list = buttons();
          if (!list.length) { return; }
          var i = list.indexOf(document.activeElement);
          var next = e.shiftKey ? (i <= 0 ? list.length - 1 : i - 1) : (i + 1) % list.length;
          e.preventDefault();
          list[next].focus();
        }
        return;
      }
      var dir = { ArrowLeft: -1, ArrowUp: -1, ArrowRight: 1, ArrowDown: 1 }[e.key];
      if (dir) {
        e.preventDefault();
        e.stopImmediatePropagation();
        var all = buttons();
        if (!all.length) { return; }
        var at = all.indexOf(document.activeElement);
        all[at < 0 ? 0 : Math.max(0, Math.min(all.length - 1, at + dir))].focus();
        return;
      }
      if (e.key === "Enter" || OK_CODES.indexOf(e.keyCode) >= 0) {
        e.preventDefault();
        e.stopImmediatePropagation();
        if (e.repeat) { return; }
        var a = document.activeElement;
        if (a && panel.contains(a) && a.tagName === "BUTTON") { a.click(); } else { keep.focus(); }
      }
    }
    function onKeyUp(e) {
      if (tv && (e.key === "Enter" || e.key === " " || OK_CODES.indexOf(e.keyCode) >= 0)) { e.stopImmediatePropagation(); e.preventDefault(); }
    }
    global.addEventListener("keydown", onKey, true);
    global.addEventListener("keyup", onKeyUp, true);
    host.addEventListener("click", function (e) { if (e.target === host && !busy) { close(); } });
    keep.focus();

    var season = opts.isShow && opts.season ? opts.season() : null;
    var asks = [NL.fetchJSON(previewUrl(opts.itemId, null)).then(function (p) { previews.show = p; })];
    if (season !== null && season !== undefined) {
      asks.push(NL.fetchJSON(previewUrl(opts.itemId, season)).then(function (p) { previews.season = p; }).catch(function () { /* no files in it */ }));
    }
    Promise.all(asks).then(function () {
      if (closed) { return; }
      if (previews.season && previews.show && previews.season.videos < previews.show.videos) {
        // A show with more than this season: choose what goes. The smaller choice is picked first.
        choices.appendChild(choice("season", "This season · " + previews.season.size));
        choices.appendChild(choice("show", "Whole show · " + previews.show.size));
        pick("season");
      } else {
        pick("show");
      }
      keep.focus();
    }).catch(function (err) {
      text.textContent = err.status === 404 ? "That is already gone from the library." : "Could not reach the app. Nothing was deleted.";
      keep.focus();
    });

    function choice(key, label) {
      var b = btn(label, "nld-choice", function () { if (!busy) { pick(key); } });
      b.setAttribute("data-key", key);
      b.setAttribute("aria-pressed", "false");
      return b;
    }

    return { close: close };
  }

  function button(opts) {
    var b = el("button", opts.tv ? "tv-btn" : "nl-btn-quiet", "Delete from server");
    b.type = "button";
    if (opts.tv) { b.setAttribute("data-nl-focus", ""); }
    b.setAttribute("aria-label", "Delete from server: " + (opts.title || "this title"));
    b.addEventListener("click", function () {
      open({
        itemId: opts.itemId, isShow: opts.isShow, season: opts.season, tv: opts.tv, onDone: opts.onDone,
        onClose: opts.tv && global.NLFocus ? function () { global.NLFocus.focus(b); } : null
      });
    });
    return b;
  }

  global.NLDelete = { button: button, open: open, sentence: sentence };
})(window);
