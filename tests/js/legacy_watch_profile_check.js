// Behaviour checks for the legacy finish modal: it uses the canonical rating
// vocabulary and sends the selected profile. tests/test_nl_people.py runs this
// under node with a small DOM stand-in.
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

let finishRequest = null;
const verdicts = [
  { id: "rewatch", label: "Would rewatch" },
  { id: "loved", label: "Loved it" },
  { id: "liked", label: "Liked it" },
  { id: "meh", label: "Meh" },
  { id: "not_for_me", label: "Not for me" },
];
const elements = {
  feedbackVerdictSelector: { innerHTML: "" },
  feedbackVerdictLabel: { innerText: "" },
  feedbackSubmitButton: { disabled: true },
  reviewNotesInput: { value: "" },
  feedbackModal: { classList: { remove() {} } },
  watchingBanner: { style: {} },
};
const document = {
  activeElement: null,
  addEventListener() {},
  getElementById(id) { return elements[id] || (elements[id] = { value: "", style: {}, classList: { remove() {} } }); },
  querySelectorAll() { return []; },
};
const ctx = {
  document,
  window: { addEventListener() {} },
  localStorage: { getItem(key) { return key === "activeProfileId" ? "2" : null; } },
  console,
  fetch: async (url, options = {}) => {
    if (url === "/api/ratings/vocab") {
      return { ok: true, json: async () => ({ verdicts }) };
    }
    finishRequest = { url, body: JSON.parse(options.body) };
    return { ok: true, json: async () => ({ success: true }) };
  },
};
ctx.globalThis = ctx;
vm.createContext(ctx);
vm.runInContext(
  fs.readFileSync(path.join(__dirname, "..", "..", "static", "app.js"), "utf8") +
    "\n;globalThis.__test = { state, initFeedbackSelector, selectFeedbackVerdict, submitFeedback }; globalThis.showToast = () => {}; globalThis.switchTab = () => {};",
  ctx,
);

function check(label, ok, detail) {
  if (ok) { console.log("  ok  " + label); return; }
  console.log("  FAIL " + label + (detail !== undefined ? " -> " + JSON.stringify(detail) : ""));
  process.exitCode = 1;
}

(async () => {
  await ctx.__test.initFeedbackSelector();
  check("loads the shared rating vocabulary", ctx.__test.state.ratingVocab?.verdicts.length === 5);
  check("shows the same five labels as the Rate page", verdicts.every(v => elements.feedbackVerdictSelector.innerHTML.includes(v.label)), elements.feedbackVerdictSelector.innerHTML);
  check("requires a choice instead of defaulting to a rating", ctx.__test.state.selectedVerdict === null && elements.feedbackSubmitButton.disabled);

  ctx.__test.state.activeSession = { session_id: 41 };
  ctx.__test.state.activeProfileId = 2;
  ctx.__test.selectFeedbackVerdict("liked");
  await ctx.__test.submitFeedback();
  check("posts to the legacy finish endpoint", finishRequest && finishRequest.url === "/api/watch/finish", finishRequest);
  check("sends the canonical verdict instead of a 1–10 number", finishRequest && finishRequest.body.verdict === "liked" && !("user_rating" in finishRequest.body), finishRequest && finishRequest.body);
  check("sends the profile selected in the legacy header", finishRequest && finishRequest.body.profile_ids.length === 1 && finishRequest.body.profile_ids[0] === 2, finishRequest && finishRequest.body);
})();
