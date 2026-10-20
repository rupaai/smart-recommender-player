/* Owns the server-side watch session for one player page. */
(function (root, factory) {
  "use strict";
  var api = factory();
  if (typeof module === "object" && module.exports) {
    module.exports = api;
  } else {
    root.NLWatchSession = api;
  }
}(typeof window !== "undefined" ? window : globalThis, function () {
  "use strict";

  function create(fetcher) {
    var startPromise = null;
    var terminalPromise = null;
    var terminalAction = null;

    function start(mediaItemId) {
      if (terminalAction) { return Promise.resolve(null); }
      if (startPromise) { return startPromise; }
      startPromise = Promise.resolve().then(function () {
        return fetcher("/api/watch/start", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ media_item_id: Number(mediaItemId) })
        });
      }).then(function (response) {
        return response && response.ok ? response.json() : null;
      }).then(function (body) {
        return body && Number(body.session_id) > 0 ? Number(body.session_id) : null;
      }).catch(function () { return null; });
      return startPromise;
    }

    function end(action) {
      if (terminalAction) { return terminalPromise || startPromise || Promise.resolve(null); }
      terminalAction = action;
      if (!startPromise) { return Promise.resolve(null); }
      terminalPromise = startPromise.then(function (sessionId) {
        if (!sessionId) { return null; }
        return Promise.resolve().then(function () {
          return fetcher("/api/watch/" + action, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ session_id: sessionId }),
            keepalive: true
          });
        }).catch(function () { return null; });
      });
      return terminalPromise;
    }

    return {
      start: start,
      finish: function () { return end("finish"); },
      cancel: function () { return end("cancel"); }
    };
  }

  return { create: create };
}));
