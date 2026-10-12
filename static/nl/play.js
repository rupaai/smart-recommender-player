/* Northern Lights M0: load one movie or episode into the player and start it. */
(function () {
  "use strict";

  var video = document.getElementById("nl-video");
  var errorBox = document.getElementById("nl-error");

  function showError(message) {
    errorBox.textContent = message;
    errorBox.style.display = "flex";
  }

  var itemId = document.body.getAttribute("data-item-id");
  var player = NLPlayerCore.create(video);
  player.on("error", function (e) { showError(e.message); });

  fetch("/api/nl/play/" + encodeURIComponent(itemId))
    .then(function (resp) {
      return resp.json().catch(function () { return {}; }).then(function (body) {
        if (!resp.ok) { throw new Error(body.detail || "Could not start playback (" + resp.status + ")."); }
        return body;
      });
    })
    .then(function (stream) {
      document.title = stream.title ? stream.title + " - Northern Lights" : document.title;
      player.load({ url: stream.url, kind: stream.kind });
      var started = player.play();
      if (started && started.catch) { started.catch(function () { /* autoplay blocked: native controls remain */ }); }
    })
    .catch(function (err) { showError(err.message); });
})();
