/* Northern Lights admin page (M10b, H4): web only. Shows /api/nl/admin: Jellyfin and disk status, the libraries
 * and where they point, a library repair button, and links to Jellystat / Uptime Kuma / Dozzle when they are
 * configured. The server sends no key, token or secret; all text goes in through textContent, and a link's address
 * is only ever used when the server validated it as http(s). */
(function () {
  "use strict";
  var NL = window.NL;
  var el = NL.el;

  function gb(bytes) {
    if (bytes === null || bytes === undefined) { return "unknown"; }
    var v = bytes / (1024 * 1024 * 1024);
    return (v >= 100 ? Math.round(v) : v.toFixed(1)) + " GB";
  }

  function row(dl, name, value, level) {
    dl.appendChild(el("dt", null, name));
    var dd = el("dd", level ? "is-" + level : null, value);
    dl.appendChild(dd);
  }

  function render(data) {
    var h = data.health || {};
    var jf = h.jellyfin || {};
    var disk = data.disk || {};
    var q = h.queue || {};
    var dl = document.getElementById("nl-adm-list");
    dl.textContent = "";
    row(dl, "Jellyfin", jf.label || "Unknown", jf.ok ? "ok" : "bad");
    if (data.jellyfin && data.jellyfin.host) { row(dl, "Jellyfin address", data.jellyfin.host); }
    var set = (data.jellyfin && data.jellyfin.settings_present) || {};
    var missing = Object.keys(set).filter(function (k) { return !set[k]; });
    row(dl, "Jellyfin settings", missing.length ? "Missing: " + missing.join(", ") : "All present (values are never shown)", missing.length ? "bad" : "ok");
    if (disk.known) {
      var used = disk.media_used_bytes === null ? "" : ", media uses " + gb(disk.media_used_bytes) + (disk.max_media_gb ? " of the " + disk.max_media_gb + " GB cap" : "");
      row(dl, "Disk", gb(disk.free_bytes) + " free of " + gb(disk.total_bytes) + used + (disk.level === "bad" ? " (below the " + disk.min_free_gb + " GB minimum: new downloads are refused)" : ""), disk.level);
    } else {
      row(dl, "Disk", "Could not be read", "warn");
    }
    row(dl, "Downloads", q.active ? q.active + " active" + (q.failed ? ", " + q.failed + " failed" : "") : (q.failed ? q.failed + " failed" : "Nothing running"), q.failed ? "warn" : "ok");

    var libs = document.getElementById("nl-adm-libs");
    libs.textContent = "";
    var info = data.jellyfin || {};
    (info.libraries || []).forEach(function (lib) {
      var li = el("li", "nl-admin-lib");
      li.appendChild(el("strong", null, lib.name || "Library"));
      li.appendChild(document.createTextNode(" " + (lib.type || "") + (lib.realtime_monitor ? " · live monitoring on" : " · live monitoring off")));
      li.appendChild(el("div", "nl-sub", (lib.locations && lib.locations.length) ? "Folders: " + lib.locations.join(", ") : "No folders attached"));
      libs.appendChild(li);
    });
    var note = document.getElementById("nl-adm-jf-note");
    if (!info.configured) { note.textContent = "Jellyfin is not configured on this server."; }
    else if (!info.server_online) { note.textContent = "Jellyfin is not answering, so its libraries can't be listed."; }
    else if (!(info.libraries || []).length) { note.textContent = "Jellyfin has no libraries yet."; }
    else { note.textContent = ""; }

    var c = data.cleanup || {};
    var cnote = document.getElementById("nl-adm-clean-note");
    var clist = document.getElementById("nl-adm-clean-list");
    clist.textContent = "";
    if (!c.enabled) {
      cnote.textContent = "Off. Watched movies and episodes are kept.";
    } else {
      var rule = "Deletes what everyone watched more than " + c.days + " days ago. Favorites are kept.";
      var last = c.last;
      if (!last) {
        cnote.textContent = rule + " First check runs 10 minutes after the app starts.";
      } else {
        var when = new Date(last.ran_at).toLocaleString();
        var head = last.dry_run ? "Trial run (nothing is deleted yet). Last check " + when + ", would delete:" : "Last check " + when + ", deleted:";
        cnote.textContent = rule + " " + head + (last.items.length ? "" : " nothing.") + (last.error ? " Problem: " + last.error : "");
        last.items.forEach(function (it) {
          var text = it.name + " · watched " + new Date(it.last_watched).toLocaleDateString() + (it.error ? " · could not delete (" + it.error + ")" : "");
          clist.appendChild(el("li", "nl-admin-lib", text));
        });
      }
    }

    var tools = data.tools || { links: [], ignored: [] };
    var list = document.getElementById("nl-adm-tool-list");
    list.textContent = "";
    tools.links.forEach(function (t) {
      var li = el("li");
      var a = el("a", "nl-btn-quiet", t.name);
      a.href = t.url;
      a.target = "_blank";
      a.rel = "noopener noreferrer";
      a.setAttribute("aria-label", "Open " + t.name + " in a new tab");
      li.appendChild(a);
      list.appendChild(li);
    });
    var tn = document.getElementById("nl-adm-tools-note");
    var bits = [];
    if (!tools.links.length) { bits.push("No tools are set up. Add Jellystat, Uptime Kuma or Dozzle addresses under northern_lights.tools in the config to get links here."); }
    if (tools.ignored.length) { bits.push("Ignored because the address is not a plain http or https link: " + tools.ignored.join(", ") + "."); }
    tn.textContent = bits.join(" ");

    document.getElementById("nl-admin").hidden = false;
    NL.setStatus("");
  }

  function load() {
    NL.fetchJSON("/api/nl/admin").then(render).catch(function () {
      NL.setStatus("Could not load the admin data. Is the app running?");
    });
  }

  var result = document.getElementById("nl-adm-repair-result");
  document.getElementById("nl-adm-check").addEventListener("click", function () { result.textContent = ""; load(); });
  document.getElementById("nl-adm-repair").addEventListener("click", function () {
    var btn = this;
    if (!window.confirm("Repair the Jellyfin libraries? It adds missing folders and asks Jellyfin to rescan. It never deletes media.")) { return; }
    btn.disabled = true;
    result.textContent = "Repairing…";
    NL.fetchJSON("/api/nl/admin/repair", { method: "POST" }).then(function (r) {
      var parts = (r.libraries || []).map(function (l) { return (l.library || "Library") + ": " + (l.result || "unknown") + (l.hint ? " (" + l.hint + ")" : ""); });
      result.textContent = (r.error ? r.error + " " : "") + parts.join(" · ") + (r.refresh_triggered ? " · rescan started" : "");
      load();
    }).catch(function (e) {
      result.textContent = "Repair failed: " + e.message;
    }).then(function () { btn.disabled = false; });
  });

  load();
})();
