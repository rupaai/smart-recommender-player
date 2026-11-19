/* Northern Lights "Save for offline" (services/nl_offline.py). A button on the title and show pages of the
 * phone/laptop web pages and the laptop player (/player), not the TV.
 *
 * The button opens a panel that asks the server what can be saved (GET /api/nl/offline/<id>[?season=]):
 * the original video (no conversion) with its size, and the subtitle files beside it. Each file is a plain
 * download link; "Save all" starts them one after another. The saved files play without the server in a video
 * app such as VLC or IINA, which load subtitles that share the video's name by themselves.
 */
(function (global) {
  "use strict";

  var BIG = 20 * 1024 * 1024 * 1024;   // above this, say plainly that the device needs the room

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) { n.className = cls; }
    if (text !== undefined && text !== null) { n.textContent = text; }
    return n;
  }

  function sizeLabel(n) {
    var gb = n / Math.pow(1024, 3);
    if (gb >= 1) { return gb.toFixed(1) + " GB"; }
    return Math.max(1, Math.round(n / Math.pow(1024, 2))) + " MB";
  }

  function listUrl(itemId, season) {
    return "/api/nl/offline/" + encodeURIComponent(itemId) +
      (season === null || season === undefined ? "" : "?season=" + encodeURIComponent(season));
  }

  /* The links "Save all" starts for these rows: each video, then its subtitle files. */
  function allLinks(files) {
    var out = [];
    (files || []).forEach(function (f) {
      out.push({ href: f.href, name: f.name });
      (f.subtitles || []).forEach(function (s) { out.push({ href: s.href, name: s.name }); });
    });
    return out;
  }

  /* A plain sentence about the size: always the total, and a warning for big ones. */
  function sizeNote(p) {
    var n = (p.files || []).length;
    var what = p.is_show ? n + (n === 1 ? " episode" : " episodes") : "The original file";
    var s = what + ", " + (p.size || sizeLabel(p.bytes || 0)) + ", same quality as at home.";
    if ((p.bytes || 0) >= BIG) { s += " That is big: check this device has the room first."; }
    return s;
  }

  function link(href, name, cls, text) {
    var a = el("a", cls, text);
    a.href = href;
    a.setAttribute("download", name);
    return a;
  }

  function start(links, onEach) {
    // One after another with a short gap: browsers drop downloads started all at once.
    links.forEach(function (l, i) {
      global.setTimeout(function () {
        var a = link(l.href, l.name, null, "");
        a.style.display = "none";
        document.body.appendChild(a);
        a.click();
        a.remove();
        if (onEach) { onEach(i + 1, links.length); }
      }, i * 1200);
    });
  }

  function open(opts) {
    var host = el("div", "nld-host nlo-host" + (opts.big ? " nld-tv" : ""));
    host.setAttribute("role", "dialog");
    host.setAttribute("aria-modal", "true");
    var panel = el("div", "nld-panel nlo-panel");
    var heading = el("h2", "nld-title", "Save for offline");
    heading.id = "nlo-title";
    host.setAttribute("aria-labelledby", heading.id);
    var text = el("p", "nld-text", "Checking what can be saved…");
    var list = el("ul", "nlo-list");
    var help = el("p", "nld-text nlo-help",
      "Files go to this device’s Downloads. Watch them without the server in VLC (free, phone and laptop) or IINA (free, Mac). " +
      "Save before you travel: downloading from far away is slow.");
    var buttons = el("div", "nld-buttons");
    var close = el("button", "nld-btn", "Close");
    close.type = "button";
    var all = el("button", "nld-btn nlo-all", "Save all");
    all.type = "button";
    all.hidden = true;
    buttons.appendChild(close);
    buttons.appendChild(all);
    panel.appendChild(heading);
    panel.appendChild(text);
    panel.appendChild(list);
    panel.appendChild(help);
    panel.appendChild(buttons);
    host.appendChild(panel);
    document.body.appendChild(host);

    var closed = false;
    function shut() {
      if (closed) { return; }
      closed = true;
      global.removeEventListener("keydown", onKey, true);
      host.remove();
      if (opts.onClose) { opts.onClose(); }
    }
    function onKey(e) {
      if (e.key === "Escape") { e.preventDefault(); e.stopImmediatePropagation(); shut(); }
    }
    global.addEventListener("keydown", onKey, true);
    host.addEventListener("click", function (e) { if (e.target === host) { shut(); } });
    close.addEventListener("click", shut);
    close.focus();

    var season = opts.isShow && opts.season ? opts.season() : null;
    global.NL.fetchJSON(listUrl(opts.itemId, season)).then(function (p) {
      if (closed) { return; }
      heading.textContent = "Save for offline: " + p.what;
      if (!p.files || !p.files.length) {
        text.textContent = "There is no file on the server to save.";
        return;
      }
      text.textContent = sizeNote(p);
      p.files.forEach(function (f) {
        var row = el("li", "nlo-row");
        var name = el("span", "nlo-name", (f.label || "Film") + " · " + f.size);
        row.appendChild(name);
        row.appendChild(link(f.href, f.name, "nld-btn nlo-save", "Save"));
        if (f.subtitles && f.subtitles.length) {
          var subs = el("span", "nlo-subs");
          subs.appendChild(document.createTextNode("Subtitles: "));
          f.subtitles.forEach(function (s, i) {
            if (i) { subs.appendChild(document.createTextNode(", ")); }
            var tail = s.name.slice(f.name.replace(/\.[^.]+$/, "").length).replace(/^\./, "");
            subs.appendChild(link(s.href, s.name, "nlo-sub", tail || s.name));
          });
          row.appendChild(subs);
        }
        list.appendChild(row);
      });
      var links = allLinks(p.files);
      if (links.length > 1) {
        all.hidden = false;
        all.textContent = "Save all · " + p.size;
        all.addEventListener("click", function () {
          all.disabled = true;
          start(links, function (done, total) {
            all.textContent = done < total ? "Starting " + done + " of " + total + "…" : "All started";
          });
          text.textContent = sizeNote(p) + " Your browser may ask to allow several downloads: choose Allow.";
        });
      }
    }).catch(function (err) {
      text.textContent = err && err.status === 404 ? "That is not on the server any more." : "Could not reach the app. Try again.";
    });

    return { close: shut };
  }

  function button(opts) {
    var b = el("button", opts.className || "nl-btn-quiet", "Save for offline");
    b.type = "button";
    if (opts.focusable) { b.setAttribute("data-nl-focus", ""); }
    b.setAttribute("aria-label", "Save for offline: " + (opts.title || "this title"));
    b.addEventListener("click", function () {
      open({ itemId: opts.itemId, isShow: opts.isShow, season: opts.season, big: opts.big, onClose: function () { b.focus(); } });
    });
    return b;
  }

  global.NLOffline = { button: button, open: open, allLinks: allLinks, sizeNote: sizeNote };
})(window);
