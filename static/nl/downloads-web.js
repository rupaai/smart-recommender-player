/* Starts the Downloads screen on the web page (the TV page starts it from tv.js). */
(function () {
  "use strict";
  window.NLDownloads.mount(document.getElementById("nl-downloads"), { tv: false });
})();
