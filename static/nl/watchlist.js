/* Northern Lights watchlist page (M6): the shared list with yes / maybe / pass for each person.
 * Titles both people said yes to come first; the runtime chips hide what is longer than you have time for. */
(function () {
  "use strict";
  var NL = window.NL;
  var host = document.getElementById("nl-watchlist");
  var count = document.getElementById("nl-count");
  var chipsHost = document.getElementById("nl-chips");
  var chipsBox = null;
  var value = window.NLWatch ? window.NLWatch.savedRuntime() : null;
  var seq = 0;

  function row(c) {
    var li = NL.el("li", "nl-wl-item");
    var poster = NL.el(c.href ? "a" : "div", "nl-poster nl-wl-poster");
    if (c.href) { poster.href = c.href; poster.setAttribute("aria-label", "Open " + c.title); }
    NL.fillImage(poster, [c.poster_url], c.title);
    li.appendChild(poster);

    var body = NL.el("div", "nl-wl-body");
    var title = NL.el("h2", "nl-wl-title");
    if (c.href) {
      var a = NL.el("a", null, c.title);
      a.href = c.href;
      title.appendChild(a);
    } else {
      title.textContent = c.title;
    }
    body.appendChild(title);
    body.appendChild(NL.el("p", "nl-meta", NL.metaLine({ year: c.year, runtime: c.runtime, media_type: c.media_type })));

    var tags = NL.el("div", "nl-wl-tags");
    if (c.both_yes) { tags.appendChild(NL.el("span", "nl-tag is-yes", "You both said yes")); }
    tags.appendChild(NL.el("span", "nl-tag " + (c.ready ? "is-ready" : "is-away"), c.ready ? "Ready" : "Not in library yet"));
    if (c.personal) { tags.appendChild(NL.el("span", "nl-tag", "Just me")); }
    body.appendChild(tags);
    if (!c.ready && c.download_href) {
      var find = NL.el("a", "nl-find", "Find a copy");
      find.href = c.download_href;
      find.setAttribute("aria-label", "Find a copy of " + c.title + " in the Download tab");
      body.appendChild(find);
    }
    if (window.NLWatch) { body.appendChild(window.NLWatch.interestControl(c.id)); }

    var remove = NL.el("button", "nl-btn-quiet nl-wl-remove", "Remove");
    remove.type = "button";
    remove.setAttribute("aria-label", "Remove " + c.title + " from the watchlist");
    remove.addEventListener("click", function () {
      window.NLWatch.remove(c.id, c.title).then(function () { li.remove(); refreshCount(); }).catch(function () {});
    });
    body.appendChild(remove);
    li.appendChild(body);
    return li;
  }

  function refreshCount() {
    var n = host.children.length;
    count.textContent = n ? n + (n === 1 ? " title" : " titles") : "";
    if (!n) { NL.setStatus(value ? "Nothing on the watchlist fits that length." : "Nothing here yet. Press + on any title to save it."); }
  }

  function render(data) {
    host.textContent = "";
    if (!chipsBox && window.NLWatch) {
      chipsBox = window.NLWatch.chips({ chips: data.chips, value: value, onChange: function (v) { value = v; load(); } });
      chipsHost.appendChild(chipsBox);
    }
    (data.items || []).forEach(function (c) { host.appendChild(row(c)); });
    NL.setStatus("");
    refreshCount();
  }

  function load() {
    var mine = ++seq;
    var url = "/api/nl/watchlist" + (value ? "?max_runtime=" + encodeURIComponent(value) : "");
    NL.fetchJSON(url).then(function (data) {
      if (mine === seq) { render(data); }
    }).catch(function () {
      if (mine === seq) { NL.setStatus("Could not load the watchlist. Is the app running?"); }
    });
  }

  load();
})();
