// Behaviour check for the legacy finish modal: the header's selected profile must
// be sent explicitly, even when an NL viewer cookie could name someone else.
// tests/test_nl_people.py runs this under node with a small DOM stand-in.
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

let request = null;
const elements = {
  reviewNotesInput: { value: "" },
  feedbackModal: { classList: { remove() {} } },
  watchingBanner: { style: {} },
};
const document = {
  activeElement: null,
  addEventListener() {},
  getElementById(id) { return elements[id] || (elements[id] = { value: "", style: {}, classList: { remove() {} } }); },
};
const ctx = {
  document,
  window: { addEventListener() {} },
  localStorage: { getItem(key) { return key === "activeProfileId" ? "2" : null; } },
  console,
  fetch: async (url, options) => {
    request = { url, body: JSON.parse(options.body) };
    return { ok: true, json: async () => ({ success: true }) };
  },
};
ctx.globalThis = ctx;
vm.createContext(ctx);
vm.runInContext(
  fs.readFileSync(path.join(__dirname, "..", "..", "static", "app.js"), "utf8") +
    "\n;globalThis.__test = { state, submitFeedback }; globalThis.showToast = () => {}; globalThis.switchTab = () => {};",
  ctx,
);

function check(label, ok, detail) {
  if (ok) { console.log("  ok  " + label); return; }
  console.log("  FAIL " + label + (detail !== undefined ? " -> " + JSON.stringify(detail) : ""));
  process.exitCode = 1;
}

(async () => {
  ctx.__test.state.activeSession = { session_id: 41 };
  ctx.__test.state.activeProfileId = 2;
  await ctx.__test.submitFeedback();
  check("posts to the legacy finish endpoint", request && request.url === "/api/watch/finish", request);
  check("sends the profile selected in the legacy header", request && request.body.profile_ids.length === 1 && request.body.profile_ids[0] === 2, request && request.body);
})();
