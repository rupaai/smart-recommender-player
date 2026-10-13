/* Northern Lights compact status (M10b, H3): one small indicator in the header with Jellyfin (up or not),
 * free disk space and the download queue. Shared by the web pages and the TV pages.
 *
 * Web: a dot and a few words; the first part links to the admin page, the queue part to Downloads.
 * TV: plain text only (it is not focusable, so it never gets in the remote's way, and there is no admin link).
 *
 * The state is never shown by colour alone: the words change too ("Jellyfin down", "Low disk"). Everything is
 * written with textContent. If the status cannot be fetched the indicator stays hidden. */
(function (global) {
  "use strict";
  var host = document.getElementById("nl-health") || document.getElementById("tv-health");
  if (!host) { return; }
  var tv = host.id === "tv-health";
  var p = tv ? "tv" : "nl";
  var POLL_MS = 30000;
  var timer = null;

  function gb(bytes) {
    var v = bytes / (1024 * 1024 * 1024);
    return (v >= 100 ? Math.round(v) : v >= 10 ? v.toFixed(0) : v.toFixed(1)) + " GB";
  }

  function part(text, href, cls) {
    var node = document.createElement(href ? "a" : "span");
    node.className = p + "-health-part" + (cls ? " " + cls : "");
    node.textContent = text;
    if (href) { node.href = href; }
    return node;
  }

  function words(data) {
    var jf = data.jellyfin || {};
    var disk = data.disk || {};
    var q = data.queue || {};
    var jfText = jf.ok ? "Jellyfin up" : (jf.authorised === false ? "Jellyfin key refused" : "Jellyfin down");
    var diskText = "";
    if (disk.known && disk.free_bytes !== null) {
      diskText = (disk.level === "bad" ? "Disk full, " : disk.level === "warn" ? "Low disk, " : "") + gb(disk.free_bytes) + " free";
    }
    var queueText = "";
    if (q.failed) { queueText = q.failed + " failed"; }
    else if (q.active) { queueText = q.active + (q.active === 1 ? " download" : " downloads"); }
    return { jf: jfText, disk: diskText, queue: queueText, jfOk: !!jf.ok, queueFailed: !!q.failed };
  }

  function render(data) {
    var w = words(data);
    host.textContent = "";
    host.setAttribute("data-level", data.level || "ok");
    var dot = document.createElement("span");
    dot.className = p + "-health-dot";
    dot.setAttribute("aria-hidden", "true");
    host.appendChild(dot);
    var main = tv ? part(w.jf, null) : part(w.jf, "/nl/admin");
    if (!w.jfOk) { main.classList.add("is-bad"); }
    host.appendChild(main);
    if (w.disk) {
      var d = part(w.disk, null, data.disk.level !== "ok" ? "is-warn" : "");
      host.appendChild(d);
    }
    if (w.queue) {
      host.appendChild(tv ? part(w.queue, null, w.queueFailed ? "is-warn" : "") : part(w.queue, "/nl/downloads", w.queueFailed ? "is-warn" : ""));
    }
    host.setAttribute("aria-label", "Status: " + [w.jf, w.disk, w.queue].filter(Boolean).join(", "));
    host.hidden = false;
  }

  function refresh() {
    clearTimeout(timer);
    if (document.hidden) { return; }
    fetch("/api/nl/health").then(function (r) {
      if (!r.ok) { throw new Error("status " + r.status); }
      return r.json();
    }).then(render).catch(function () { host.hidden = true; }).then(function () {
      timer = setTimeout(refresh, POLL_MS);
    });
  }

  document.addEventListener("visibilitychange", function () { if (!document.hidden) { refresh(); } });
  global.NLHealth = { refresh: refresh };
  refresh();
})(window);
