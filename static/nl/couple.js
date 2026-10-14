/* Northern Lights couple extras (M9): the small pieces the web and TV pages share.
 *   NLCouple.togetherBadge(tv)             the "Together" mark for a title or episode seen together (U4)
 *   NLCouple.pactSwitch(itemId, pact)      "We watch this together" switch for a show page (U5)
 *   NLCouple.unwatchLabels(people)         [{person, label}] for "Mark unwatched for me / <name>" (U6)
 *   NLCouple.unwatch(itemId, sourceId, p)  asks the server to reset ONE person's watched state (U6)
 * Text always goes in through textContent; nothing here talks to Jellyfin. */
(function () {
  "use strict";
  var NL = window.NL;

  function json(method, url, body) {
    return NL.fetchJSON(url, {
      method: method,
      headers: { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body)
    });
  }

  /* A small mark: two overlapping rings (drawn in CSS) and the word "Together". */
  function togetherBadge(tv) {
    var b = NL.el("span", tv ? "tv-together" : "nl-together");
    b.setAttribute("title", "Watched together");
    b.appendChild(NL.el("span", "nl-together-rings"));
    b.lastChild.setAttribute("aria-hidden", "true");
    b.appendChild(NL.el("span", "nl-together-text", "Together"));
    return b;
  }

  /* "We watch this together": on a show page, only when two people are configured. Returns null otherwise. */
  function pactSwitch(itemId, pact) {
    if (!pact || !pact.available) { return null; }
    var wrap = NL.el("div", "nl-switch");
    var box = document.createElement("button");
    box.type = "button";
    box.className = "nl-switch-btn";
    box.setAttribute("role", "switch");
    box.setAttribute("aria-checked", String(!!pact.together));
    box.appendChild(NL.el("span", "nl-switch-knob"));
    var label = NL.el("span", "nl-switch-label", "We watch this together");
    label.id = "nl-pact-label";
    box.setAttribute("aria-labelledby", "nl-pact-label");
    var note = NL.el("span", "nl-visually-hidden");
    note.setAttribute("role", "status");
    box.addEventListener("click", function () {
      var want = box.getAttribute("aria-checked") !== "true";
      box.setAttribute("aria-checked", String(want));      // optimistic; rolled back on failure
      json("PUT", "/api/nl/show/" + encodeURIComponent(itemId) + "/pact", { together: want }).then(function (r) {
        pact.together = r.together;
        box.setAttribute("aria-checked", String(!!r.together));
        note.textContent = r.together ? "You will be asked before watching this alone" : "Watching alone is fine";
      }).catch(function () {
        box.setAttribute("aria-checked", String(!want));
        note.textContent = "Couldn’t save that";
      });
    });
    label.addEventListener("click", function () { box.click(); });
    wrap.appendChild(box);
    wrap.appendChild(label);
    wrap.appendChild(note);
    return wrap;
  }

  /* One entry per person the viewer can reset. A single viewer sees "Mark unwatched for me";
   * The shared group sees one per person, by name. No people configured: one, for the only viewer. */
  function unwatchLabels(people) {
    people = people || [];
    if (people.length <= 1) { return [{ person: people[0] ? people[0].id : null, label: "Mark unwatched for me" }]; }
    return people.map(function (p) { return { person: p.id, label: "Mark unwatched for " + p.name }; });
  }

  function unwatch(itemId, sourceId, person) {
    var body = {};
    if (sourceId) { body.source_id = sourceId; }
    if (person) { body.person = person; }
    return json("POST", "/api/nl/unwatch/" + encodeURIComponent(itemId), body);
  }

  window.NLCouple = { togetherBadge: togetherBadge, pactSwitch: pactSwitch, unwatchLabels: unwatchLabels, unwatch: unwatch };
})();
