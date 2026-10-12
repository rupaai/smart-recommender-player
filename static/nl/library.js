/* Northern Lights M1: a plain poster grid of the movies we can play. */
(function () {
  "use strict";

  var grid = document.getElementById("nl-grid");
  var status = document.getElementById("nl-status");
  var count = document.getElementById("nl-count");

  function meta(m) {
    var bits = [];
    if (m.year) { bits.push(m.year); }
    if (m.runtime) {
      var h = Math.floor(m.runtime / 60), min = m.runtime % 60;
      bits.push(h ? h + " h " + (min ? min + " min" : "") : min + " min");
    }
    return bits.join(" · ").trim();
  }

  function card(m) {
    var li = document.createElement("li");
    var a = document.createElement("a");
    a.className = "nl-card";
    a.href = "/nl/play/" + encodeURIComponent(m.id);
    var poster = document.createElement("div");
    poster.className = "nl-poster";
    function fallback() {
      poster.innerHTML = "";
      var f = document.createElement("div");
      f.className = "nl-fallback";
      f.textContent = m.title;
      poster.appendChild(f);
    }
    if (m.poster_url) {
      var img = document.createElement("img");
      img.alt = "";
      img.loading = "lazy";
      img.src = m.poster_url;
      img.addEventListener("error", fallback);
      poster.appendChild(img);
    } else {
      fallback();
    }
    var name = document.createElement("div");
    name.className = "nl-name";
    name.textContent = m.title;
    var sub = document.createElement("div");
    sub.className = "nl-meta";
    sub.textContent = meta(m);
    a.appendChild(poster);
    a.appendChild(name);
    a.appendChild(sub);
    a.setAttribute("aria-label", m.title + (m.year ? " (" + m.year + ")" : ""));
    li.appendChild(a);
    return li;
  }

  fetch("/api/nl/library?type=movie")
    .then(function (r) { if (!r.ok) { throw new Error("status " + r.status); } return r.json(); })
    .then(function (data) {
      var items = data.items || [];
      if (!items.length) {
        status.textContent = "No movies on the shelf yet.";
        return;
      }
      status.textContent = "";
      count.textContent = items.length + (items.length === 1 ? " movie" : " movies");
      items.forEach(function (m) { grid.appendChild(card(m)); });
    })
    .catch(function () { status.textContent = "Could not load the list. Is the app running?"; });
})();
