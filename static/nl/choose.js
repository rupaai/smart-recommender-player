/* Northern Lights "Choose for us" (M9b): mood, genre and time available in, one big pick out.
 * One module for the phone page and the TV page (`opts.tv` adds the D-pad focus markers and the TV classes).
 *   NLChoose.mount(root, {tv: bool, href: fn(link) -> link, status: fn(text)})
 * Everything the screen says comes from /api/nl/choose/options and /api/nl/choose; names come from the config.
 * Text always goes in through textContent. */
(function () {
  "use strict";
  var NL = window.NL;
  var el = NL.el;

  function tzOffset() {
    try { return -new Date().getTimezoneOffset(); } catch (e) { return 0; }
  }
  function post(url, body) {
    return NL.fetchJSON(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}), keepalive: true });
  }

  function mount(root, opts) {
    opts = opts || {};
    var tv = !!opts.tv;
    var link = opts.href || function (h) { return h; };
    var cls = tv
      ? { chip: "tv-pill", main: "tv-btn is-primary", btn: "tv-btn", group: "tv-choose-group", pick: "tv-pick" }
      : { chip: "nl-chip", main: "nl-btn-main", btn: "nl-btn-quiet", group: "nl-chips nl-choose-group", pick: "nl-pick" };
    var opt = null;                       // /api/nl/choose/options
    var sel = { mood: null, genre: null, minutes: null };
    var shown = [];                       // ids "Another" must not repeat
    var current = null;                   // the pick on screen
    var undo = null;                      // {label, run} after a veto
    var rows = {};
    var busy = false;

    function focusable(node, rowName) {
      if (tv) { node.setAttribute("data-nl-focus", ""); }
      return node;
    }
    function row(name, className) {
      var box = el("div", className || cls.group);
      if (tv) { box.setAttribute("data-nl-row", name); }
      rows[name] = box;
      return box;
    }
    function say(text) {
      if (rows.status) { rows.status.textContent = text || ""; }
      if (opts.status) { opts.status(text || ""); }
    }

    /* single-choice chips; choosing the lit chip again clears it */
    function chipGroup(name, label, list, key, valueOf, textOf) {
      var box = row(name);
      box.setAttribute("role", "group");
      box.setAttribute("aria-label", label);
      function paint() {
        Array.prototype.forEach.call(box.children, function (b) {
          b.setAttribute("aria-pressed", String(b.getAttribute("data-value") === String(sel[key])));
        });
      }
      list.forEach(function (item) {
        var b = el("button", cls.chip, textOf(item));
        b.type = "button";
        b.setAttribute("data-value", String(valueOf(item)));
        focusable(b);
        b.addEventListener("click", function () {
          sel[key] = String(sel[key]) === String(valueOf(item)) ? null : valueOf(item);
          paint();
        });
        box.appendChild(b);
      });
      paint();
      return box;
    }

    function section(title, node) {
      var wrap = el("div", tv ? "tv-choose-block" : "nl-choose-block");
      wrap.appendChild(el("h2", "nl-choose-label", title));
      wrap.appendChild(node);
      return wrap;
    }

    function button(text, className, onClick, act) {
      var b = el("button", className, text);
      b.type = "button";
      b.setAttribute("data-act", act || "");
      focusable(b);
      b.addEventListener("click", onClick);
      return b;
    }

    /* ------------------------------------------------------------ the pick */

    function remember(act) {
      var a = document.activeElement;
      return act || (a && a.getAttribute ? a.getAttribute("data-act") : "") || "";
    }

    function renderPick(data, keepAct) {
      var box = rows.pick;
      box.textContent = "";
      current = data.item || null;
      if (!current) {
        box.appendChild(el("p", "nl-choose-note", data.note || "Nothing fits right now."));
        say("");
        return;
      }
      if (shown.indexOf(current.id) < 0) { shown.push(current.id); }
      var card = el("div", cls.pick + "-card");
      var poster = el("div", tv ? "tv-art tv-poster tv-pick-poster" : "nl-poster nl-pick-poster");
      NL.fillImage(poster, [current.poster_url, current.image_fallback], current.title);
      card.appendChild(poster);
      var info = el("div", cls.pick + "-info");
      info.appendChild(el("h2", cls.pick + "-title", current.title));
      var bits = [];
      if (current.year) { bits.push(String(current.year)); }
      if (current.runtime) { bits.push(NL.fmtRuntime(current.runtime)); }
      if ((current.genres || []).length) { bits.push(current.genres.slice(0, 3).join(", ")); }
      info.appendChild(el("p", tv ? "tv-meta" : "nl-meta", bits.join(" · ")));
      if (current.reason) { info.appendChild(el("p", cls.pick + "-reason", current.reason)); }
      if (current.overview && !tv) { info.appendChild(el("p", "nl-overview nl-pick-overview", current.overview)); }
      var acts = el("div", tv ? "tv-actions tv-pick-actions" : "nl-actions");
      if (tv) { acts.setAttribute("data-nl-row", "pick"); }
      var play = el("a", cls.main, "Play");
      play.href = link(current.play_href);
      play.setAttribute("data-act", "play");
      focusable(play);
      play.addEventListener("click", function () {
        // remember who chose (the next time says whose turn it is); the page leaves at once, keepalive finishes the call
        try { post("/api/nl/choose/chosen/" + encodeURIComponent(current.id)).catch(function () { /* best effort */ }); } catch (e) { /* ignore */ }
      });
      acts.appendChild(play);
      acts.appendChild(button("Another", cls.btn, function () { run("pick", "another"); }, "another"));
      acts.appendChild(button("Not tonight", cls.btn, function () { veto("tonight"); }, "tonight"));
      var people = (opt && opt.people) || [];
      if (people.length > 1) {
        people.forEach(function (p) {
          acts.appendChild(button("Never for " + p.name, cls.btn, function () { veto("never", p.id); }, "never-" + p.id));
        });
      } else {
        acts.appendChild(button("Never", cls.btn, function () { veto("never"); }, "never"));
      }
      info.appendChild(acts);
      card.appendChild(info);
      box.appendChild(card);
      if (data.remaining === 0 && data.engine !== "yeses") { box.appendChild(el("p", tv ? "tv-hint" : "nl-meta", "That is the last one that fits.")); }
      say("");
      focusAct(keepAct || "play");
    }

    function focusAct(act) {
      if (!tv) { return; }
      var target = rows.pick.querySelector('[data-act="' + act + '"]') || rows.pick.querySelector('[data-act="play"]');
      if (target) { window.NLFocus.focus(target); }
    }

    function run(mode, how, afterRender) {
      if (busy) { return; }
      busy = true;
      var keep = remember(how === "another" ? "another" : "");
      var body = {
        mode: mode, tz: tzOffset(), exclude: how === "another" ? shown.slice(-60) : [],
        mood: sel.mood ? Number(sel.mood) : null, genre: sel.genre, max_minutes: sel.minutes ? Number(sel.minutes) : null
      };
      if (how !== "another") { shown = []; }
      say("Looking…");
      post("/api/nl/choose", body).then(function (data) {
        busy = false;
        if (!data.item && how === "another" && shown.length) {
          // everything that fits has been shown: start the round again
          shown = [];
          rows.pick.textContent = "";
          rows.pick.appendChild(el("p", "nl-choose-note", "That was everything that fits. Press Another to go round again."));
          say("");
          return;
        }
        renderPick(data, keep);
        renderUsed(data);
        if (data.turn) { renderTurn(data.turn); }
        if (afterRender) { afterRender(); }
      }).catch(function () {
        busy = false;
        say("Could not choose right now. Is the app running?");
      });
    }

    function veto(kind, person) {
      if (!current || busy) { return; }
      var item = current;
      post("/api/nl/veto/" + encodeURIComponent(item.id), { kind: kind, person: person || null, tz: tzOffset() }).then(function () {
        undo = { item: item, kind: kind, person: person || null };
        run("pick", "another", function () {
          showUndo(kind === "tonight" ? item.title + " is off the table until tomorrow morning." : item.title + " won’t be suggested again.");
        });
      }).catch(function () { say("Could not save that. Try again."); });
    }

    function showUndo(text) {
      if (!undo || !rows.undo) { return; }
      var u = undo;
      rows.undo.textContent = "";
      rows.undo.appendChild(el("span", tv ? "tv-meta" : "nl-meta", text + " "));
      var b = el("button", cls.btn, "Undo");
      b.type = "button";
      focusable(b);
      b.addEventListener("click", function () {
        var q = "?kind=" + encodeURIComponent(u.kind) + (u.person ? "&person=" + encodeURIComponent(u.person) : "");
        NL.fetchJSON("/api/nl/veto/" + encodeURIComponent(u.item.id) + q, { method: "DELETE" }).then(function () {
          rows.undo.textContent = u.item.title + " is back.";
          undo = null;
        }).catch(function () { rows.undo.textContent = "Could not undo that."; });
      });
      rows.undo.appendChild(b);
    }

    function renderTurn(turn) {
      if (!rows.turn) { return; }
      rows.turn.textContent = turn ? turn.text : "";
      rows.turn.hidden = !turn;
    }

    function renderUsed(data) {
      if (!rows.used) { return; }
      var u = data.used || {};
      var c = data.context || {};
      var bits = [];
      if (!u.mood_chosen && c.mood_name) { bits.push("It’s " + c.label + ", so I leaned towards " + c.mood_name + (c.duration && !u.max_minutes ? " and something short" : "")); }
      rows.used.textContent = bits.join(". ");
    }

    /* ------------------------------------------------------------- page */

    function build(data) {
      opt = data;
      root.textContent = "";
      var head = el("div", tv ? "tv-choose-head" : "nl-page-head");
      head.appendChild(el("h1", tv ? "tv-choose-title" : null, "Choose for us"));
      var turn = el("p", tv ? "tv-choose-turn" : "nl-choose-turn");
      turn.setAttribute("role", "status");
      rows.turn = turn;
      head.appendChild(turn);
      var ctx = data.context || {};
      var used = el("p", tv ? "tv-meta" : "nl-sub");
      rows.used = used;
      used.textContent = "It’s " + (ctx.label || "today") + ". Pick what you like, or leave it and I will suggest " + (ctx.mood_name || "something") + ".";
      head.appendChild(used);
      root.appendChild(head);
      renderTurn(data.turn);

      root.appendChild(section("Mood", chipGroup("mood", "Mood", data.moods || [], "mood", function (m) { return m.id; }, function (m) { return m.label; })));
      if ((data.genres || []).length) {
        root.appendChild(section("Genre", chipGroup("genre", "Genre", data.genres, "genre", function (g) { return g; }, function (g) { return g; })));
      }
      root.appendChild(section("Time available", chipGroup("time", "Time available", data.times || [], "minutes", function (t) { return t.minutes; }, function (t) { return t.label; })));

      var actions = el("div", tv ? "tv-actions tv-choose-actions" : "nl-actions");
      if (tv) { actions.setAttribute("data-nl-row", "go"); }
      rows.go = actions;
      actions.appendChild(button("Choose for us", cls.main, function () { run("pick", ""); }, "go"));
      if (data.yeses > 0) {
        actions.appendChild(button(data.solo ? "Surprise me from my yeses" : "Surprise us from our yeses", cls.btn, function () { run("yeses", ""); }, "yeses"));
      }
      root.appendChild(actions);

      var undoBox = el("p", "nl-choose-undo");
      undoBox.setAttribute("role", "status");
      rows.undo = undoBox;
      if (tv) { undoBox.setAttribute("data-nl-row", "undo"); }
      var status = el("p", tv ? "tv-hint" : "nl-meta");
      status.setAttribute("role", "status");
      rows.status = status;
      var pick = el("div", cls.pick);
      pick.setAttribute("aria-live", "polite");
      rows.pick = pick;
      root.appendChild(status);
      root.appendChild(pick);
      root.appendChild(undoBox);
      root.hidden = false;
      NL.setStatus("");
      if (tv) {
        var st = document.getElementById("tv-status");
        if (st) { st.hidden = true; }
        window.NLFocus.start();
      }
    }

    NL.fetchJSON("/api/nl/choose/options?tz=" + encodeURIComponent(tzOffset())).then(build).catch(function () {
      var msg = "Could not load Choose for us. Is the app running?";
      if (tv) { var st = document.getElementById("tv-status"); if (st) { st.hidden = false; st.textContent = msg; } window.NLFocus.start(); } else { NL.setStatus(msg); }
    });
  }

  /* The titles this screen vetoed ("Not tonight" until tomorrow morning, each person's "Never") with a way back.
   * Shown on the me pages; the box stays hidden when there is nothing to bring back. */
  function vetoList(box, tv) {
    function load() {
      NL.fetchJSON("/api/nl/vetoes").then(function (data) {
        box.textContent = "";
        var items = data.items || [];
        box.hidden = !items.length;
        if (!items.length) { return; }
        box.appendChild(el("h2", tv ? null : "nl-me-h2", "Left out of your picks"));
        var list = el("ul", tv ? "tv-veto-list" : "nl-veto-list");
        items.forEach(function (v) {
          var li = el("li", tv ? "tv-veto" : "nl-veto");
          var what = v.kind === "tonight" ? "Not tonight" : (v.person_name && data.both ? "Never for " + v.person_name : "Never");
          li.appendChild(el("span", "nl-veto-title", v.title + (v.year ? " (" + v.year + ")" : "")));
          li.appendChild(el("span", tv ? "tv-meta" : "nl-meta", what));
          var b = el("button", tv ? "tv-btn" : "nl-btn-quiet", "Bring back");
          b.type = "button";
          if (tv) { li.setAttribute("data-nl-row", "veto-" + v.kind + "-" + v.media_item_id + "-" + (v.person || "all")); b.setAttribute("data-nl-focus", ""); }
          b.setAttribute("aria-label", "Bring back " + v.title);
          b.addEventListener("click", function () {
            var q = "?kind=" + encodeURIComponent(v.kind) + (v.person ? "&person=" + encodeURIComponent(v.person) : "");
            NL.fetchJSON("/api/nl/veto/" + encodeURIComponent(v.media_item_id) + q, { method: "DELETE" }).then(function () {
              var next = li.nextElementSibling || li.previousElementSibling;
              li.remove();
              if (!list.children.length) { box.hidden = true; }
              var t = next && next.querySelector("button");
              if (tv && t) { window.NLFocus.focus(t); }
            }).catch(function () { b.textContent = "Try again"; });
          });
          li.appendChild(b);
          list.appendChild(li);
        });
        box.appendChild(list);
      }).catch(function () { box.hidden = true; });
    }
    load();
  }

  window.NLChoose = { mount: mount, vetoList: vetoList };

  var host = document.getElementById("nl-choose");
  if (host && !host.hasAttribute("data-tv")) { mount(host, { tv: false }); }
})();
