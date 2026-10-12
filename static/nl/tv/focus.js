/* Northern Lights TV focus manager (T2): rows x columns, no dependencies.
 *
 * Markup: a row is any element with `data-nl-row="<name>"`; the things you can land on are the
 * `data-nl-focus` elements inside it (links, buttons, inputs). Rows are read from the page on every
 * key press, so pages can add, remove or hide rows at any time.
 *   <ul data-nl-row="movies"><li><a data-nl-focus href="...">...</a></li>...</ul>
 *   `data-nl-skip-initial` on a row: Up reaches it, but start() does not land there first.
 *   `data-nl-input` on an input: it is read-only until OK is pressed (so the TV keyboard does not
 *   pop up just because the focus passes over it). OK opens the keyboard; OK again (or the keyboard's
 *   search key) closes it and fires a bubbling "nl-input-done" event; Back closes it without that.
 *
 * Keys: arrows move (left/right inside a row, up/down between rows, remembering the column of each
 * row), Enter / OK / DPAD center = click, Back (Escape, Backspace, "GoBack", Android keyCode 4,
 * webOS 461, Tizen 10009) = NLFocus.onBack handlers first, then history.back().
 *
 *   NLFocus.start()          focus the remembered spot of this page, else the first card
 *   NLFocus.focus(el)        focus one element (and scroll it into view)
 *   NLFocus.onBack(fn)       fn() returns true when it handled Back (a menu was closed, say)
 *   NLFocus.remember(false)  stop saving the spot (search results change every time)
 */
(function (global) {
  "use strict";

  var BACK_CODES = [4, 27, 461, 10009];
  var ENTER_CODES = [13, 23, 66];
  var backHandlers = [];
  var rememberSpot = true;

  function qsa(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }

  function visible(el) {
    if (!el || el.disabled || el.hidden || el.closest("[hidden]")) { return false; }
    return !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
  }

  function rows() {
    return qsa("[data-nl-row]").filter(function (row) { return items(row).length > 0; });
  }
  function items(row) {
    return qsa("[data-nl-focus]", row).filter(visible);
  }
  function rowOf(el) { return el && el.closest ? el.closest("[data-nl-row]") : null; }

  function remInPx() { return parseFloat(window.getComputedStyle(document.documentElement).fontSize) || 16; }

  /* ----------------------------------------------------------------- scrolling */

  function scrollIntoComfort(el) {
    var row = rowOf(el);
    var rem = remInPx();
    if (row && row.scrollWidth > row.clientWidth + 1) {
      var r = el.getBoundingClientRect();
      var rr = row.getBoundingClientRect();
      var pad = rr.width * 0.12;
      if (r.left < rr.left + pad || r.right > rr.right - pad) {
        var delta = (r.left + r.width / 2) - (rr.left + rr.width / 2);
        row.scrollTo({ left: row.scrollLeft + delta, behavior: "smooth" });
      }
    }
    var box = el.getBoundingClientRect();
    if (box.top < 2 * rem || box.bottom > window.innerHeight - 2 * rem) {
      var section = (row && (row.closest("[data-nl-section]") || row.parentElement)) || el;
      var top = section.getBoundingClientRect().top + window.pageYOffset - 4 * rem;
      var all = rows();
      if (all[0] === row || all.indexOf(row) <= 0) { top = 0; }
      window.scrollTo({ top: Math.max(0, top), behavior: "smooth" });
    }
  }

  function focusEl(el) {
    if (!el) { return; }
    try { el.focus({ preventScroll: true }); } catch (e) { el.focus(); }
    scrollIntoComfort(el);
  }

  /* ------------------------------------------------------------ remembered spot */

  function spotKey() { return "nl.tv.spot:" + window.location.pathname + window.location.search; }
  function saveSpot(row, col) {
    if (!rememberSpot) { return; }
    try { window.sessionStorage.setItem(spotKey(), JSON.stringify({ row: row.getAttribute("data-nl-row"), col: col })); } catch (e) { /* private mode */ }
  }
  function loadSpot() {
    try {
      var raw = window.sessionStorage.getItem(spotKey());
      return raw ? JSON.parse(raw) : null;
    } catch (e) { return null; }
  }

  document.addEventListener("focusin", function (e) {
    var row = rowOf(e.target);
    if (!row || !e.target.hasAttribute("data-nl-focus")) { return; }
    var col = items(row).indexOf(e.target);
    if (col < 0) { return; }
    row._nlCol = col;
    saveSpot(row, col);
  });

  /* ------------------------------------------------------------------ movement */

  function centerX(el) { var r = el.getBoundingClientRect(); return r.left + r.width / 2; }

  function nearest(list, x) {
    var best = list[0], gap = Infinity;
    list.forEach(function (el) {
      var d = Math.abs(centerX(el) - x);
      if (d < gap) { gap = d; best = el; }
    });
    return best;
  }

  function move(dir) {
    var active = document.activeElement;
    var row = rowOf(active);
    var all = rows();
    if (!row || all.indexOf(row) < 0) { start(); return; }
    var list = items(row);
    var col = list.indexOf(active);
    if (dir === "left" || dir === "right") {
      var next = list[col + (dir === "right" ? 1 : -1)];
      if (next) { focusEl(next); }
      return;
    }
    var ri = all.indexOf(row) + (dir === "down" ? 1 : -1);
    if (ri < 0 || ri >= all.length) { return; }
    var target = all[ri];
    var tlist = items(target);
    var pick;
    if (typeof target._nlCol === "number" && target._nlCol < tlist.length) {
      pick = tlist[target._nlCol];
    } else {
      pick = nearest(tlist, centerX(active));
    }
    focusEl(pick);
  }

  function start() {
    var all = rows();
    if (!all.length) { return; }
    var spot = loadSpot();
    if (spot) {
      var row = all.filter(function (r) { return r.getAttribute("data-nl-row") === spot.row; })[0];
      if (row) {
        var list = items(row);
        if (list.length) {
          row._nlCol = Math.min(spot.col, list.length - 1);
          focusEl(list[row._nlCol]);
          return;
        }
      }
    }
    var first = all.filter(function (r) { return !r.hasAttribute("data-nl-skip-initial"); })[0] || all[0];
    focusEl(items(first)[0]);
  }

  /* ----------------------------------------------------------------- text input */

  function editing(el) {
    return !!el && el.tagName === "INPUT" && el.hasAttribute("data-nl-input") && !el.readOnly;
  }
  function beginEdit(input) {
    input.readOnly = false;
    input.focus();
    try { input.click(); } catch (e) { /* ignore */ }
    var end = input.value.length;
    try { input.setSelectionRange(end, end); } catch (e) { /* type=search may refuse */ }
  }
  function endEdit(input, done) {
    input.readOnly = true;
    if (done) { input.dispatchEvent(new CustomEvent("nl-input-done", { bubbles: true })); }
  }
  document.addEventListener("focusout", function (e) {
    if (editing(e.target)) { e.target.readOnly = true; }
  });

  /* ---------------------------------------------------------------------- keys */

  function isBack(e) {
    var k = e.key;
    if (k === "Escape" || k === "GoBack" || k === "BrowserBack" || k === "XF86Back") { return true; }
    if (k === "Backspace" && !editing(e.target)) { return true; }
    var c = e.keyCode;
    return BACK_CODES.indexOf(c) >= 0 || (c === 8 && !editing(e.target));
  }
  function isEnter(e) { return e.key === "Enter" || ENTER_CODES.indexOf(e.keyCode) >= 0; }

  function goBack() {
    for (var i = backHandlers.length - 1; i >= 0; i--) {
      try { if (backHandlers[i]() === true) { return; } } catch (e) { console.error(e); }
    }
    window.history.back();
  }

  document.addEventListener("keydown", function (e) {
    if (e.ctrlKey || e.metaKey || e.altKey) { return; }
    var active = document.activeElement;

    if (isBack(e)) {
      e.preventDefault();
      if (editing(active)) { endEdit(active, false); return; }
      if (!e.repeat) { goBack(); }
      return;
    }

    if (isEnter(e)) {
      if (active && active.hasAttribute && active.hasAttribute("data-nl-input")) {
        e.preventDefault();
        if (e.repeat) { return; }
        if (active.readOnly) { beginEdit(active); } else { endEdit(active, true); }
        return;
      }
      if (active && active.hasAttribute && active.hasAttribute("data-nl-focus")) {
        e.preventDefault();
        if (!e.repeat) { active.click(); }
      }
      return;
    }

    var dir = { ArrowLeft: "left", ArrowRight: "right", ArrowUp: "up", ArrowDown: "down" }[e.key];
    if (!dir) { return; }
    if (editing(active) && (dir === "left" || dir === "right")) { return; } // caret movement
    e.preventDefault();
    move(dir);
  });

  global.NLFocus = {
    start: start,
    focus: focusEl,
    onBack: function (fn) { backHandlers.push(fn); },
    remember: function (on) { rememberSpot = !!on; },
    goBack: goBack
  };
})(window);
