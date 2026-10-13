/* Northern Lights "me" page (M4, U3): the person's audio / subtitle language and "subtitles on by default".
 * Saved per person through NLPrefs (/api/nl/prefs). Language names are fixed strings from prefs.js. */
(function () {
  "use strict";
  var audio = document.getElementById("nl-pref-audio");
  var sub = document.getElementById("nl-pref-sub");
  var on = document.getElementById("nl-pref-on");
  var status = document.getElementById("nl-pref-status");

  function fill(select, current) {
    select.textContent = "";
    var none = document.createElement("option");
    none.value = "";
    none.textContent = "No preference";
    select.appendChild(none);
    window.NLPrefs.languages.forEach(function (l) {
      var o = document.createElement("option");
      o.value = l[0];
      o.textContent = l[1];
      select.appendChild(o);
    });
    select.value = current || "";
  }
  function say(text) { status.textContent = text; }
  function save(patch) {
    say("Saving…");
    window.NLPrefs.save(patch).then(function () { say("Saved"); });
  }

  var vetoes = document.getElementById("nl-vetoes");
  if (vetoes && window.NLChoose) { window.NLChoose.vetoList(vetoes, false); }   // M9b: bring back what was vetoed

  window.NLPrefs.get().then(function (p) {
    fill(audio, p.audio_lang);
    fill(sub, p.subtitle_lang);
    on.setAttribute("aria-checked", String(!!p.subtitles_on));
  });
  audio.addEventListener("change", function () { save({ audio_lang: audio.value || null }); });
  sub.addEventListener("change", function () { save({ subtitle_lang: sub.value || null }); });
  on.addEventListener("click", function () {
    var next = on.getAttribute("aria-checked") !== "true";
    on.setAttribute("aria-checked", String(next));
    save({ subtitles_on: next });
  });
})();
