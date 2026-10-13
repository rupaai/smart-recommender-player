/* Northern Lights Downloads screen (M10a, H1 and H2), shared by the web page and the TV page.
 *
 *   NLDownloads.mount(host, {tv: bool, href: fn(path) -> path, status: fn(text)})
 *
 * Reads GET /api/nl/downloads every few seconds and draws one row per download: queued, downloading (percent,
 * speed, time left), importing, ready (Play) or failed (the reason in plain words, Retry). Rows are updated in
 * place, so a remote's focus never jumps; a row is rebuilt only when its state changes. All text goes in
 * through textContent. The magnet link is never sent to the browser. */
(function (global) {
  "use strict";
  var NL = global.NL;
  var el = NL.el;

  function fmtBytes(n) {
    if (!n || n < 0) { return ""; }
    if (n < 1024 * 1024) { return Math.max(1, Math.round(n / 1024)) + " KB"; }
    if (n < 1024 * 1024 * 1024) { return (n / (1024 * 1024)).toFixed(n < 100 * 1024 * 1024 ? 1 : 0) + " MB"; }
    return (n / (1024 * 1024 * 1024)).toFixed(2) + " GB";
  }
  function fmtSpeed(n) { return n > 0 ? fmtBytes(n) + "/s" : ""; }
  function fmtEta(s) {
    if (s === null || s === undefined) { return ""; }
    if (s < 60) { return "under a minute left"; }
    var m = Math.floor(s / 60);
    if (m < 60) { return m + " min left"; }
    var h = Math.floor(m / 60);
    return h + " h " + (m % 60 < 10 ? "0" : "") + (m % 60) + " min left";
  }

  function detailLine(d) {
    var bits = [];
    if (d.state === "downloading") {
      bits.push(Math.round(d.percent) + "%");
      if (d.size_bytes) { bits.push(fmtBytes(d.downloaded_bytes) + " of " + fmtBytes(d.size_bytes)); }
      var speed = fmtSpeed(d.speed_bytes);
      if (speed) { bits.push(speed); }
      var eta = fmtEta(d.eta_seconds);
      if (eta) { bits.push(eta); }
      if (bits.length === 1) { bits.push("working out the speed"); }
    } else if (d.note) {
      bits.push(d.note);
    }
    return bits.join(" · ");
  }

  function mount(host, opts) {
    opts = opts || {};
    var tv = !!opts.tv;
    var p = tv ? "tv" : "nl";
    var href = opts.href || function (x) { return x; };
    var setStatus = opts.status || NL.setStatus;
    var rows = {};          // id -> {li, state, refs}
    var timer = null;
    var announce = el("p", tv ? "tv-visually-hidden" : "nl-visually-hidden");
    announce.setAttribute("role", "status");
    announce.setAttribute("aria-live", "polite");
    var list = el("ul", p + "-dl-list");
    var empty = el("p", p + "-dl-empty", "");
    empty.hidden = true;
    host.appendChild(announce);
    host.appendChild(list);
    host.appendChild(empty);
    var first = true;

    function button(label, aria, cls) {
      var b = el("button", cls, label);
      b.type = "button";
      b.setAttribute("aria-label", aria);
      if (tv) { b.setAttribute("data-nl-focus", ""); }
      return b;
    }
    function link(label, aria, to, cls) {
      var a = el("a", cls, label);
      a.href = href(to);
      a.setAttribute("aria-label", aria);
      if (tv) { a.setAttribute("data-nl-focus", ""); }
      return a;
    }

    function build(d) {
      var li = el("li", p + "-dl-item is-" + d.state);
      var main = el("div", p + "-dl-main");
      var head = el("h2", p + "-dl-title", d.title);
      var tag = el("span", p + "-dl-tag", d.label);
      var line = el("p", p + "-dl-meta", "");
      var bar = el("div", p + "-dl-bar");
      bar.setAttribute("role", "progressbar");
      bar.setAttribute("aria-label", d.title + " progress");
      bar.setAttribute("aria-valuemin", "0");
      bar.setAttribute("aria-valuemax", "100");
      var fill = el("span", p + "-dl-fill");
      bar.appendChild(fill);
      var err = el("p", p + "-dl-error", "");
      err.setAttribute("role", "alert");
      main.appendChild(head);
      main.appendChild(tag);
      main.appendChild(line);
      main.appendChild(bar);
      main.appendChild(err);
      li.appendChild(main);

      var acts = el("div", p + "-dl-actions");
      if (tv) { acts.setAttribute("data-nl-row", "dl-" + d.id); }
      if (d.state === "ready") {
        if (d.play_href) { acts.appendChild(link("Play", "Play " + d.title, d.play_href, tv ? "tv-btn is-primary" : "nl-btn-main")); }
        acts.appendChild(link(d.media_type === "tv" ? "Open show" : "Details", "Open " + d.title, d.href, tv ? "tv-btn" : "nl-btn-quiet"));
      }
      if (d.can_retry) {
        var retry = button("Retry", "Retry downloading " + d.title, tv ? "tv-btn is-primary" : "nl-btn-main");
        retry.addEventListener("click", function () { act(d.id, "POST", "/api/nl/downloads/" + d.id + "/retry", retry, err); });
        acts.appendChild(retry);
      }
      if (d.can_dismiss) {
        var rm = button("Remove", "Remove " + d.title + " from the list", tv ? "tv-btn" : "nl-btn-quiet");
        rm.addEventListener("click", function () { act(d.id, "DELETE", "/api/nl/downloads/" + d.id, rm, err); });
        acts.appendChild(rm);
      }
      li.appendChild(acts);
      return { li: li, state: d.state, retry: !!d.can_retry, fill: fill, bar: bar, line: line, tag: tag, err: err, id: d.id, title: d.title };
    }

    function paint(row, d) {
      row.line.textContent = detailLine(d);
      row.line.hidden = !row.line.textContent;
      row.fill.style.width = d.percent + "%";
      row.bar.setAttribute("aria-valuenow", String(Math.round(d.percent)));
      row.bar.hidden = d.state === "failed" || d.state === "cancelled" || d.state === "ready";
      row.err.textContent = d.error || "";
      row.err.hidden = !d.error;
      row.tag.textContent = d.label;
    }

    function act(id, method, url, btn, errBox) {
      btn.disabled = true;
      fetch(url, { method: method }).then(function (r) {
        return r.json().catch(function () { return {}; }).then(function (body) {
          if (!r.ok) { throw new Error(body.detail || "That didn't work."); }
        });
      }).then(function () { refresh(); }).catch(function (e) {
        btn.disabled = false;
        errBox.textContent = e.message;
        errBox.hidden = false;
      });
    }

    function render(data) {
      var downloads = data.downloads || [];
      var seen = {};
      var focusedId = null;
      var active = document.activeElement;
      Object.keys(rows).forEach(function (k) { if (rows[k].li.contains(active)) { focusedId = k; } });
      var refocus = null;
      downloads.forEach(function (d, i) {
        seen[d.id] = true;
        var row = rows[d.id];
        if (row && (row.state !== d.state || row.retry !== !!d.can_retry)) {
          var old = row;
          row = build(d);
          old.li.replaceWith(row.li);
          rows[d.id] = row;
          if (String(focusedId) === String(d.id)) { refocus = row.li; }
          if (!first) { announceChange(d); }
        } else if (!row) {
          row = build(d);
          rows[d.id] = row;
          if (!first) { announceChange(d); }
        }
        paint(row, d);
        // keep the server's order
        if (list.children[i] !== row.li) { list.insertBefore(row.li, list.children[i] || null); }
      });
      Object.keys(rows).forEach(function (k) {
        if (!seen[k]) {
          var gone = rows[k];
          if (gone.li.contains(document.activeElement)) { refocus = list.children[0] || null; }
          gone.li.remove();
          delete rows[k];
        }
      });
      empty.textContent = "No downloads right now. Search for a title that isn't in the library yet and choose Find a copy to start one.";
      empty.hidden = downloads.length > 0;
      setStatus("");
      if (tv && refocus) {
        var target = refocus.querySelector("[data-nl-focus]");
        if (target) { global.NLFocus.focus(target); }
      }
      if (tv && first) { global.NLFocus.start(); }
      first = false;
    }

    function announceChange(d) {
      if (d.state === "ready") { announce.textContent = d.title + " is ready to watch."; }
      else if (d.state === "failed") { announce.textContent = d.title + " failed. " + (d.error || ""); }
    }

    function schedule(ms) {
      clearTimeout(timer);
      timer = setTimeout(refresh, ms);
    }

    function refresh() {
      clearTimeout(timer);
      if (document.hidden) { return; }
      NL.fetchJSON("/api/nl/downloads").then(function (data) {
        render(data);
        schedule((data.counts && data.counts.active) ? 2000 : 5000);
      }).catch(function () {
        if (first) { setStatus("Could not load the downloads. Is the app running?"); if (tv) { global.NLFocus.start(); } first = false; }
        schedule(8000);
      });
    }

    document.addEventListener("visibilitychange", function () { if (!document.hidden) { refresh(); } });
    refresh();
  }

  global.NLDownloads = { mount: mount, fmtEta: fmtEta, fmtBytes: fmtBytes, fmtSpeed: fmtSpeed, detailLine: detailLine };
})(window);
