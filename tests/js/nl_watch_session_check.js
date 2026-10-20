// Watch-session lifecycle behavior used by the Northern Lights player.
"use strict";
const fs = require("fs");
const path = require("path");
const createWatchSession = require(path.join(__dirname, "..", "..", "static", "nl", "watch-session.js")).create;

const calls = [];
function fakeFetch(url, options) {
  calls.push({ url, options });
  if (url === "/api/watch/start") {
    return Promise.resolve({ ok: true, json: async () => ({ session_id: 42 }) });
  }
  return Promise.resolve({ ok: true });
}

function check(label, condition, detail) {
  if (condition) { console.log("  ok  " + label); return; }
  console.log("  FAIL " + label + (detail !== undefined ? " -> " + JSON.stringify(detail) : ""));
  process.exitCode = 1;
}

(async () => {
  const session = createWatchSession(fakeFetch);
  const ids = await Promise.all([session.start("17"), session.start(17)]);
  check("starting twice creates one watch session", ids[0] === 42 && ids[1] === 42 && calls.length === 1, calls);
  check("start sends the numeric media item id", calls[0].options.body === JSON.stringify({ media_item_id: 17 }), calls[0]);

  await Promise.all([session.finish(), session.cancel()]);
  check("completed playback finishes the retained session id", calls.length === 2 && calls[1].url === "/api/watch/finish" && calls[1].options.body === JSON.stringify({ session_id: 42 }), calls);
  check("terminal request survives page navigation", calls[1].options.keepalive === true);

  calls.length = 0;
  const abandoned = createWatchSession(fakeFetch);
  await abandoned.start(18);
  await Promise.all([abandoned.cancel(), abandoned.finish()]);
  check("early exit cancels and cannot later mark the session watched", calls.length === 2 && calls[1].url === "/api/watch/cancel", calls);

  calls.length = 0;
  const alreadyClosed = createWatchSession(fakeFetch);
  await alreadyClosed.cancel();
  const closedStart = await alreadyClosed.start(18);
  check("a terminal page cannot start a new orphaned session", closedStart === null && calls.length === 0, calls);

  calls.length = 0;
  let resolveStart;
  const pending = createWatchSession((url, options) => {
    calls.push({ url, options });
    if (url === "/api/watch/start") {
      return new Promise(resolve => { resolveStart = () => resolve({ ok: true, json: async () => ({ session_id: 91 }) }); });
    }
    return Promise.resolve({ ok: true });
  });
  const started = pending.start(19);
  const cancelled = pending.cancel();
  await Promise.resolve();
  resolveStart();
  await Promise.all([started, cancelled]);
  check("early exit racing the start response cancels once the session id arrives", calls.length === 2 && calls[1].url === "/api/watch/cancel" && calls[1].options.body === JSON.stringify({ session_id: 91 }), calls);

  const player = fs.readFileSync(path.join(__dirname, "..", "..", "static", "nl", "player.js"), "utf8");
  const template = fs.readFileSync(path.join(__dirname, "..", "..", "templates", "nl", "player.html"), "utf8");
  const next = player.slice(player.indexOf("function advanceToNext()"), player.indexOf("function cancelNext()"));
  const ended = player.slice(player.indexOf('player.on("ended"'), player.indexOf("/* --------------------------------------------- after finishing"));
  const leave = player.slice(player.indexOf("function leave(completed)"), player.indexOf('window.addEventListener("pagehide"'));
  const pagehide = player.slice(player.indexOf('window.addEventListener("pagehide"'), player.indexOf('document.addEventListener("visibilitychange"'));
  check("watch-session helper loads before the player", template.indexOf("/static/nl/watch-session.js") < template.indexOf("/static/nl/player.js"));
  check("autoplaying the next episode leaves the show session open", next.includes("startFirst({ fromNext: true })") && !next.includes("watchSession.finish()"));
  check("final playback completion finishes the show session", ended.includes("!S.next") && ended.includes("watchSession.finish()"));
  check("leaving early cancels and completed cast playback finishes", leave.includes("watchSession.cancel()") && leave.includes("watchSession.finish()"));
  check("pagehide cancels a session when playback is not casting", pagehide.includes("watchSession.cancel()"));
})();
