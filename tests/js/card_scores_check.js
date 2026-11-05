// Behaviour check for the card scores: IMDb and Rotten Tomatoes first, TMDB only while IMDb is unknown.
// static/nl/nl.js (scoreFacts: title pages on the web and the TV) and static/app.js (scoreMeta: the original
// app's cards). Plain node; tests/test_card_scores.py calls it when node exists.
//   node tests/js/card_scores_check.js
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

let failed = 0;
function check(label, ok, detail) {
  if (ok) { console.log("  ok  " + label); } else { failed += 1; console.log("  FAIL " + label + (detail !== undefined ? " -> " + JSON.stringify(detail) : "")); }
}

const root = path.join(__dirname, "..", "..");
const doc = { getElementById() { return null; }, addEventListener() {}, createElement() { return {}; } };
const sandbox = { window: {}, document: doc, console };
vm.runInNewContext(fs.readFileSync(path.join(root, "static", "nl", "nl.js"), "utf8"), sandbox);
const scoreFacts = sandbox.window.NL.scoreFacts;

check("IMDb and RT shown", JSON.stringify(scoreFacts({ imdb: 7.8, rt: 93, tmdb: null })) === JSON.stringify(["IMDb 7.8", "RT 93%"]));
check("RT 0% is still shown", scoreFacts({ imdb: 5.1, rt: 0 }).includes("RT 0%"));
check("TMDB only while IMDb is unknown", JSON.stringify(scoreFacts({ imdb: null, rt: null, tmdb: 7.5 })) === JSON.stringify(["TMDB 7.5"]));
check("TMDB hidden once IMDb is known", !scoreFacts({ imdb: 8, rt: null, tmdb: 7.5 }).some((f) => f.startsWith("TMDB")));
check("no scores, nothing shown", scoreFacts(undefined).length === 0 && scoreFacts({}).length === 0);

// The original app: take scoreMeta's source out of app.js and run it with the two helpers it uses.
const app = fs.readFileSync(path.join(root, "static", "app.js"), "utf8");
const m = app.match(/function scoreMeta\(m\) \{[\s\S]*?\n\}/);
check("scoreMeta exists in app.js", !!m);
const ctx = { escapeHtml: (s) => String(s).replace(/</g, "&lt;"), icon: () => "*" };
vm.runInNewContext(m[0] + "; this.scoreMeta = scoreMeta;", ctx);
const both = ctx.scoreMeta({ imdb_rating: 7.84, rt_score: 91, rating: 7.1 }).join(" ");
check("card shows IMDb and RT", both.includes("IMDb 7.8") && both.includes("RT 91%"), both);
check("card hides TMDB once IMDb is known", !both.includes("7.1"), both);
const tmdb = ctx.scoreMeta({ rating: 7.1 }).join(" ");
check("card falls back to TMDB, labelled", tmdb.includes("7.1") && tmdb.includes("TMDB"), tmdb);

// Our own score is only a small side note, never a badge.
check("no 'Together' score badge", !app.includes('title="Your combined score">Together'));
check("our score is a side note", app.includes('class="ours-note"'));
check("no internal numbers in the taste badge's tooltip", !app.includes("facet fit ${"));

if (failed) { console.log(failed + " failed"); process.exit(1); }
console.log("all passed");
