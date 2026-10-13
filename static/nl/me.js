/* Northern Lights "me" page (M4, U3; M10c, H5): the person's playback defaults (languages, subtitles on, skip length,
 * autoplay, quality) and display settings (picture size, hide spoilers). Saved per person through NLPrefs
 * (/api/nl/prefs). Names and choices are fixed strings from prefs.js. */
(function () {
  "use strict";
  var audio = document.getElementById("nl-pref-audio");
  var sub = document.getElementById("nl-pref-sub");
  var on = document.getElementById("nl-pref-on");
  var status = document.getElementById("nl-pref-status");
  var spoilers = document.getElementById("nl-pref-spoilers");
  var skip = document.getElementById("nl-pref-skip");
  var autoplay = document.getElementById("nl-pref-autoplay");
  var quality = document.getElementById("nl-pref-quality");
  var fit = document.getElementById("nl-pref-fit");

  function fillChoices(select, pairs, current, none) {
    select.textContent = "";
    pairs.forEach(function (pair) {
      var o = document.createElement("option");
      o.value = pair[0] === null ? "" : String(pair[0]);
      o.textContent = pair[1];
      select.appendChild(o);
    });
    select.value = current === null || current === undefined ? (none || "") : String(current);
  }

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
    spoilers.setAttribute("aria-checked", String(!!p.hide_spoilers));
    fillChoices(skip, [[null, "My device's own choice"]].concat(window.NLPrefs.skipChoices.map(function (v) { return [v, v + " seconds"]; })), p.skip_seconds);
    fillChoices(quality, window.NLPrefs.qualityChoices, p.quality_kbps);
    fillChoices(fit, window.NLPrefs.fitChoices, p.picture_fit, "fit");
    autoplay.setAttribute("aria-checked", String(p.autoplay !== false));
  });
  skip.addEventListener("change", function () { save({ skip_seconds: skip.value ? Number(skip.value) : null }); });
  quality.addEventListener("change", function () { save({ quality_kbps: quality.value ? Number(quality.value) : null }); });
  fit.addEventListener("change", function () { save({ picture_fit: fit.value || "fit" }); });
  autoplay.addEventListener("click", function () {
    var next = autoplay.getAttribute("aria-checked") !== "true";
    autoplay.setAttribute("aria-checked", String(next));
    save({ autoplay: next });
  });
  spoilers.addEventListener("click", function () {
    var next = spoilers.getAttribute("aria-checked") !== "true";
    spoilers.setAttribute("aria-checked", String(next));
    save({ hide_spoilers: next });   // M9c: per viewer, kept on the server
  });
  audio.addEventListener("change", function () { save({ audio_lang: audio.value || null }); });
  sub.addEventListener("change", function () { save({ subtitle_lang: sub.value || null }); });
  on.addEventListener("click", function () {
    var next = on.getAttribute("aria-checked") !== "true";
    on.setAttribute("aria-checked", String(next));
    save({ subtitles_on: next });
  });
})();
