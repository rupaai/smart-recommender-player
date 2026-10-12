/* Northern Lights home (M2): Continue Watching, then Movies and Shows rows. */
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

  NL.fetchJSON("/api/nl/home").then(function (data) {
    var any = false;
    any = fill("nl-continue", "nl-continue-list", data.continue_watching, NL.wideCard) || any;
    any = fill("nl-movies", "nl-movies-list", data.movies, NL.posterCard) || any;
    any = fill("nl-shows", "nl-shows-list", data.shows, NL.posterCard) || any;
    document.getElementById("nl-movies-all").textContent = "See all " + (data.movies_total || "");
    document.getElementById("nl-shows-all").textContent = "See all " + (data.shows_total || "");
    NL.setStatus(any ? "" : "Nothing on the shelf yet. Add something from the Download tab of the main app.");
  }).catch(function () {
    NL.setStatus("Could not load the library. Is the app running?");
  });
})();
