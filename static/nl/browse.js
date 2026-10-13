/* Northern Lights Movies and Shows pages (M2): a poster grid with simple cards.
 * M7b: an "Unwatched only" switch (remembered per list) that asks the server for the titles nobody at the screen has seen. */
(function () {
  "use strict";
  var NL = window.NL;
  var grid = document.getElementById("nl-grid");
  var kind = grid.getAttribute("data-kind");
  var noun = kind === "tv" ? ["show", "shows"] : ["movie", "movies"];
  var count = document.getElementById("nl-count");
  var host = document.getElementById("nl-filters");
  var seq = 0;
  var toggle = null;

  function load(unwatched) {
    var mine = ++seq;
    var url = "/api/nl/library?type=" + encodeURIComponent(kind) + (unwatched ? "&unwatched=1" : "");
    return NL.fetchJSON(url).then(function (data) {
      if (mine !== seq) { return; }
      var items = data.items || [];
      grid.textContent = "";
      var note = "";
      if (unwatched && !data.unwatched_applied) { note = "Jellyfin isn’t answering, so I can’t tell what you’ve watched. Showing everything."; }
      if (!items.length) {
        count.textContent = "";
        NL.setStatus(unwatched && data.unwatched_applied ? "You’ve watched every " + noun[0] + " on the shelf." : "No " + noun[1] + " on the shelf yet.");
        return;
      }
      NL.setStatus(note);
      count.textContent = items.length + " " + noun[items.length === 1 ? 0 : 1] + (unwatched && data.unwatched_applied ? ", unwatched" : "");
      items.forEach(function (it) { grid.appendChild(NL.posterCard(it)); });
    }).catch(function () {
      if (mine === seq) { NL.setStatus("Could not load the list. Is the app running?"); }
    });
  }

  if (host && window.NLPolish) {
    toggle = window.NLPolish.unwatchedSwitch({ key: "nl.unwatched." + kind, onChange: load });
    host.appendChild(toggle);
  }
  load(!!(toggle && toggle.nlOn));
})();
