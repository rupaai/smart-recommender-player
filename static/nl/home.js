/* Northern Lights home (M2): Continue Watching, New episodes, then Movies and Shows rows. */
(function () {
  "use strict";
  var NL = window.NL;

  function fill(sectionId, listId, items, build) {
    var section = document.getElementById(sectionId);
    var list = document.getElementById(listId);
    if (!items || !items.length) { section.hidden = true; return false; }
    items.forEach(function (it) { list.appendChild(build(it)); });
    section.hidden = false;
    return true;
  }

  // M7b: the poster rows on home can be hidden per title (Continue watching cannot).
  function hideable(it) { it.hideable = true; return NL.posterCard(it); }

  // New episodes to download (its own call: TMDB answers can take a moment the first time).
  if (window.NLNew) {
    window.NLNew.homeRow(document.getElementById("nl-new-row"), document.getElementById("nl-new-row-list"), NL.posterCard, { root: "/nl/" });
  }

  // Today's films from what you watched lately (made once a day on the server).
  if (window.NLDaily) {
    window.NLDaily.homeRow(document.getElementById("nl-daily"), document.getElementById("nl-daily-list"), {});
  }

  NL.fetchJSON("/api/nl/home").then(function (data) {
    var any = false;
    any = fill("nl-continue", "nl-continue-list", data.continue_watching, NL.wideCard) || any;
    any = fill("nl-recent-added", "nl-recent-added-list", data.recently_added, hideable) || any;
    any = fill("nl-recent-watched", "nl-recent-watched-list", data.recently_watched, hideable) || any;
    any = fill("nl-movies", "nl-movies-list", data.movies, hideable) || any;
    any = fill("nl-shows", "nl-shows-list", data.shows, hideable) || any;
    document.getElementById("nl-movies-all").textContent = "See all " + (data.movies_total || "");
    document.getElementById("nl-shows-all").textContent = "See all " + (data.shows_total || "");
    NL.setStatus(any ? "" : "Nothing on the shelf yet. Add something from the Download tab of the main app.");
  }).catch(function () {
    NL.setStatus("Could not load the library. Is the app running?");
  });
})();
