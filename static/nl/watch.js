/* Northern Lights watchlist and "yes / maybe / pass" (M6), shared by the phone/laptop pages and the TV pages.
 *
 * One module holds what the server says (GET /api/nl/watchlist/state) and the buttons built from it:
 *   NLWatch.iconButton(id, title)   "+" on a card or search row (shared by default; a small choice when "Just me" is offered)
 *   NLWatch.fullControl(id, title)  "+ Watchlist" / "Just me" / "On the watchlist" for a details page
 *   NLWatch.interestControl(id)     yes / maybe / pass for each person (only your own answer can be changed)
 *   NLWatch.cycleButton(id, person) TV: one button per person, OK steps through yes, maybe, pass, none
 *   NLWatch.chips(opts)             the runtime chips "Under 90 min" and "Under 2 h" (press the lit one to clear it)
 * Every button repaints when any other one changes the same title. Text goes in through textContent only.
 * The server decides everything (who may see what); this file never sees a Jellyfin id or key. */
(function () {
  "use strict";
  var NL = window.NL;
  var el = NL.el;
  var state = null;       // the last good answer from the server; null = unknown (buttons stay hidden)
  var loading = null;
  var STATES = ["yes", "maybe", "pass"];
  var LABEL = { yes: "Yes", maybe: "Maybe", pass: "Pass" };

  function load(force) {
    if (!loading || force) {
      loading = NL.fetchJSON("/api/nl/watchlist/state").then(function (s) { state = s; return s; }).catch(function () { state = null; return null; });
    }
    return loading;
  }

  function request(method, url, body) {
    var opts = { method: method };
    if (body) { opts.headers = { "Content-Type": "application/json" }; opts.body = JSON.stringify(body); }
    return NL.fetchJSON(url, opts);
  }

  function peopleStub() {
    return ((state && state.people) || []).map(function (p) { return { person: p.id, name: p.name, state: null, mine: p.mine }; });
  }
  function isListed(id) { return !!(state && state.listed[String(id)]); }
  function isPersonal(id) { return !!(state && state.listed[String(id)] && state.listed[String(id)].personal); }
  function answers(id) { return (state && state.interest[String(id)]) || peopleStub(); }
  function nameOf(p) { return p.name === "You" ? "You" : p.name; }

  /* ------------------------------------------------------------ toast */

  var toastTimer = null;
  function say(message) {
    var t = document.getElementById("nl-toast");
    if (!t) {
      t = el("div", "nl-toast");
      t.id = "nl-toast";
      t.setAttribute("role", "status");
      document.body.appendChild(t);
    }
    t.textContent = message;
    t.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { t.hidden = true; }, 3600);
  }

  /* --------------------------------------------------- change fan-out */

  var repainters = [];
  function onChange(fn, node) { repainters.push({ fn: fn, node: node }); }
  function changed(id) {
    repainters = repainters.filter(function (r) { return !r.node || document.documentElement.contains(r.node); });
    repainters.forEach(function (r) { r.fn(String(id)); });
  }
  function apply(item) {
    if (!state) { return item; }
    var key = String(item.media_item_id);
    if (item.listed) { state.listed[key] = { personal: !!item.personal }; } else { delete state.listed[key]; }
    state.interest[key] = item.interest || [];
    changed(key);
    return item;
  }

  function add(id, scope, title) {
    return request("POST", "/api/nl/watchlist", { media_item_id: id, scope: scope || "shared" }).then(function (r) {
      apply(r);
      say(scope === "me" ? "Added for just you" + (title ? ": " + title : "") : (state && state.solo ? "Added to your watchlist" : "Added to the " + document.body.dataset.groupLabel + " watchlist") + (title ? ": " + title : ""));
      return r;
    }).catch(function (err) { say("Couldn’t add that. " + (err && err.message ? err.message : "")); throw err; });
  }
  function remove(id, title) {
    return request("DELETE", "/api/nl/watchlist/" + encodeURIComponent(id)).then(function (r) {
      apply(r);
      say("Removed from the watchlist" + (title ? ": " + title : ""));
      return r;
    }).catch(function (err) { say("Couldn’t remove that."); throw err; });
  }
  function setInterest(id, person, value) {
    return request("PUT", "/api/nl/interest/" + encodeURIComponent(id), { state: value, person: person }).then(apply).catch(function (err) {
      say("Couldn’t save that answer.");
      throw err;
    });
  }

  /* ------------------------------------------------------ icon button */

  function closeMenus() {
    Array.prototype.forEach.call(document.querySelectorAll(".nl-watch-menu"), function (m) { m.remove(); });
  }
  document.addEventListener("pointerdown", function (e) { if (!e.target.closest || !e.target.closest(".nl-watch-menu, .nl-watch-icon")) { closeMenus(); } });
  document.addEventListener("keydown", function (e) { if (e.key === "Escape") { closeMenus(); } });

  function iconButton(id, title) {
    var b = el("button", "nl-watch-icon");
    b.type = "button";
    b.hidden = true;
    function paint() {
      if (!state) { b.hidden = true; return; }
      b.hidden = false;
      var on = isListed(id);
      b.setAttribute("aria-pressed", String(on));
      b.textContent = on ? "✓" : "+";
      b.title = on ? "On the watchlist. Press to remove." : "Add to the watchlist";
      b.setAttribute("aria-label", (on ? "On the watchlist, remove: " : "Add to watchlist: ") + title);
    }
    function menu() {
      closeMenus();
      var m = el("div", "nl-watch-menu");
      m.setAttribute("role", "group");
      m.setAttribute("aria-label", "Add " + title + " to the watchlist");
      var both = el("button", null, document.body.dataset.groupLabel);
      both.type = "button";
      var me = el("button", null, "Just me");
      me.type = "button";
      both.addEventListener("click", function () { m.remove(); add(id, "shared", title).catch(function () {}); });
      me.addEventListener("click", function () { m.remove(); add(id, "me", title).catch(function () {}); });
      m.appendChild(both);
      m.appendChild(me);
      b.parentNode.appendChild(m);
      both.focus();
    }
    b.addEventListener("click", function (e) {
      e.preventDefault();
      e.stopPropagation();
      if (!state) { return; }
      if (isListed(id)) { remove(id, title).catch(function () {}); return; }
      if (state.can_personal) { menu(); return; }
      add(id, "shared", title).catch(function () {});
    });
    onChange(function (changedId) { if (changedId === String(id)) { paint(); } }, b);
    load().then(paint);
    paint();
    return b;
  }

  /* ----------------------------------------------------- full control */

  function fullControl(id, title, cls) {
    var box = el("div", "nl-watch-full");
    var btnClass = cls || "nl-btn-quiet";
    function btn(text, fn) {
      var b = el("button", btnClass, text);
      b.type = "button";
      b.addEventListener("click", fn);
      return b;
    }
    function paint() {
      box.textContent = "";
      if (!state) { box.hidden = true; return; }
      box.hidden = false;
      if (isListed(id)) {
        var on = btn("On the watchlist ✓", function () { remove(id, title).catch(function () {}); });
        on.setAttribute("aria-label", "On the watchlist. Remove " + title);
        on.setAttribute("data-nl-focus", "");
        box.appendChild(on);
        box.appendChild(el("span", "nl-meta nl-watch-note", isPersonal(id) ? "Only you can see this" : (state.solo ? "" : "Shared")));
        return;
      }
      var a = btn("+ Watchlist", function () { add(id, "shared", title).catch(function () {}); });
      a.setAttribute("aria-label", (state.solo ? "Add to your watchlist: " : "Add to the shared watchlist: ") + title);
      a.setAttribute("data-nl-focus", "");
      box.appendChild(a);
      if (state.can_personal) {
        var me = btn("Just me", function () { add(id, "me", title).catch(function () {}); });
        me.setAttribute("aria-label", "Add to the watchlist for just you: " + title);
        me.setAttribute("data-nl-focus", "");
        box.appendChild(me);
      }
    }
    onChange(function (changedId) { if (changedId === String(id)) { var f = document.activeElement; var was = f && box.contains(f); paint(); if (was) { var first = box.querySelector("button"); if (first) { first.focus(); } } } }, box);
    load().then(paint);
    paint();
    return box;
  }

  /* ------------------------------------------------ yes / maybe / pass */

  function interestControl(id) {
    var box = el("div", "nl-interest");
    box.setAttribute("role", "group");
    box.setAttribute("aria-label", "Would you watch this?");
    function paint() {
      box.textContent = "";
      if (!state) { box.hidden = true; return; }
      box.hidden = false;
      answers(id).forEach(function (p) {
        var row = el("div", "nl-interest-row");
        row.appendChild(el("span", "nl-interest-who", nameOf(p)));
        if (p.mine) {
          var group = el("div", "nl-interest-btns");
          group.setAttribute("role", "group");
          group.setAttribute("aria-label", nameOf(p) + ": would you watch this?");
          STATES.forEach(function (s) {
            var b = el("button", "nl-interest-btn", LABEL[s]);
            b.type = "button";
            b.setAttribute("data-state", s);
            b.setAttribute("aria-pressed", String(p.state === s));
            b.addEventListener("click", function () { setInterest(id, p.person, p.state === s ? null : s).catch(function () {}); });
            group.appendChild(b);
          });
          row.appendChild(group);
        } else {
          row.appendChild(el("span", "nl-interest-read", p.state ? LABEL[p.state] : "No answer yet"));
        }
        box.appendChild(row);
      });
    }
    onChange(function (changedId) { if (changedId === String(id)) { var f = document.activeElement; var at = f && box.contains(f) ? f.getAttribute("data-state") : null; paint(); if (at) { var again = box.querySelector('[data-state="' + at + '"]'); if (again) { again.focus(); } } } }, box);
    load().then(paint);
    paint();
    return box;
  }

  /* TV: a D-pad has no button groups, so one button per person and OK steps through the answers. */
  function nextState(cur) { var i = STATES.indexOf(cur); return i === STATES.length - 1 ? null : STATES[i + 1]; }

  function cycleButton(id, person, cls) {
    var b = el("button", cls || "tv-btn");
    b.type = "button";
    function find() { return answers(id).filter(function (p) { return p.person === person; })[0]; }
    function paint() {
      var p = find();
      if (!state || !p) { b.hidden = true; return; }
      b.hidden = false;
      b.textContent = nameOf(p) + ": " + (p.state ? LABEL[p.state] : "Not yet");
      b.setAttribute("aria-label", nameOf(p) + ": " + (p.state ? LABEL[p.state] : "no answer yet") + ". OK changes it.");
    }
    b.addEventListener("click", function () {
      var p = find();
      if (!p) { return; }
      setInterest(id, person, nextState(p.state)).catch(function () {});
    });
    onChange(function (changedId) { if (changedId === String(id)) { paint(); } }, b);
    load().then(paint);
    paint();
    return b;
  }

  /* --------------------------------------------------- runtime chips */

  function savedRuntime() {
    try { var v = parseInt(window.localStorage.getItem("nl.maxRuntime"), 10); return v > 0 ? v : null; } catch (e) { return null; }
  }
  function saveRuntime(v) {
    try { if (v) { window.localStorage.setItem("nl.maxRuntime", String(v)); } else { window.localStorage.removeItem("nl.maxRuntime"); } } catch (e) { /* private mode */ }
  }

  /* opts: {chips: [{minutes, label}], value, onChange(minutes|null), cls, focusable, label}. Returns the element; .set(v) repaints. */
  function chips(opts) {
    var box = el("div", opts.groupClass || "nl-chips");
    box.setAttribute("role", "group");
    box.setAttribute("aria-label", opts.label || "How long do you have?");
    var value = opts.value || null;
    function paint() {
      Array.prototype.forEach.call(box.children, function (b) { b.setAttribute("aria-pressed", String(Number(b.getAttribute("data-minutes")) === value)); });
    }
    (opts.chips || []).forEach(function (c) {
      var b = el("button", opts.cls || "nl-chip", c.label);
      b.type = "button";
      b.setAttribute("data-minutes", c.minutes);
      if (opts.focusable) { b.setAttribute("data-nl-focus", ""); }
      b.addEventListener("click", function () {
        value = value === c.minutes ? null : c.minutes;
        saveRuntime(value);
        paint();
        opts.onChange(value);
      });
      box.appendChild(b);
    });
    paint();
    box.set = function (v) { value = v; paint(); };
    return box;
  }

  window.NLWatch = {
    load: load, state: function () { return state; }, add: add, remove: remove, setInterest: setInterest,
    iconButton: iconButton, fullControl: fullControl, interestControl: interestControl, cycleButton: cycleButton,
    chips: chips, savedRuntime: savedRuntime, saveRuntime: saveRuntime, say: say, answers: answers, isListed: isListed
  };
  load();
})();
