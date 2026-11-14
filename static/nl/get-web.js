/* Starts the Get it page on the web (the TV and laptop pages start it from tv.js). */
(function () {
  "use strict";
  window.NLGet.mount(document.getElementById("nl-get"), { tv: false, self: "/nl/get" });
})();
