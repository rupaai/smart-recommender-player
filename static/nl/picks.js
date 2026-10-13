/* Northern Lights home row "For both of us" / "Picks for you" (M6).
 * The server asks the recommender in co-view mode for whoever is watching and answers with library titles,
 * each with one line saying why. A slow or failing recommender only delays or hides this row; the rest of
 * the home page does not wait for it. */
(function () {
  "use strict";
  var NL = window.NL;
  var section = document.getElementById("nl-picks");
  if (!section) { return; }
  var list = document.getElementById("nl-picks-list");
  var note = document.getElementById("nl-picks-note");
  var heading = document.getElementById("nl-picks-h");
  var chipsHost = document.getElementById("nl-picks-chips");
  var empty = document.getElementById("nl-picks-empty");
  var chipsBox = null;
  var seq = 0;
  var value = window.NLWatch ? window.NLWatch.savedRuntime() : null;

  function card(item) {
    item.hideable = true;   // M7b: "Hide from home" works on this row too
    var li = NL.posterCard(item);
    var why = NL.el("p", "nl-reason", item.reason || "");
    why.setAttribute("data-reason", "");
    li.appendChild(why);
    return li;
  }

  function render(data) {
    heading.textContent = data.title || "For both of us";
    note.textContent = data.note || "";
    list.textContent = "";
    var items = data.items || [];
    items.forEach(function (it) { list.appendChild(card(it)); });
    if (!chipsBox && window.NLWatch) {
      chipsBox = window.NLWatch.chips({ chips: data.chips, value: value, onChange: function (v) { value = v; fetchPicks(); } });
      chipsHost.appendChild(chipsBox);
    }
    if (items.length) {
      empty.hidden = true;
    } else {
      empty.hidden = false;
      empty.textContent = value ? "Nothing on the shelf fits that length." : "";
    }
    // Nothing to show and no filter set: the row stays out of the way.
    section.hidden = !items.length && !value;
  }

  function fetchPicks() {
    var mine = ++seq;
    var url = "/api/nl/picks" + (value ? "?max_runtime=" + encodeURIComponent(value) : "");
    return NL.fetchJSON(url).then(function (data) {
      if (mine === seq) { render(data); }
    }).catch(function () {
      if (mine === seq) { section.hidden = true; }
    });
  }

  fetchPicks();
})();
