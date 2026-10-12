/* Northern Lights Movies and Shows pages (M2): a poster grid with simple cards. */
(function () {
  "use strict";
  var NL = window.NL;
  var grid = document.getElementById("nl-grid");
  var kind = grid.getAttribute("data-kind");
  var noun = kind === "tv" ? ["show", "shows"] : ["movie", "movies"];

  NL.fetchJSON("/api/nl/library?type=" + encodeURIComponent(kind)).then(function (data) {
    var items = data.items || [];
    if (!items.length) {
      NL.setStatus("No " + noun[1] + " on the shelf yet.");
      return;
    }
    NL.setStatus("");
    document.getElementById("nl-count").textContent = items.length + " " + noun[items.length === 1 ? 0 : 1];
    items.forEach(function (it) { grid.appendChild(NL.posterCard(it)); });
  }).catch(function () {
    NL.setStatus("Could not load the list. Is the app running?");
  });
})();
