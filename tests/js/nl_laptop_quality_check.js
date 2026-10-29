// Behaviour check for the laptop's original-quality request: NLPlayerExtras.playDeviceParams (the client / caps /
// allow_conversion part of /api/nl/play) and how player.js uses it (convert button, picture-subtitle question).
// Plain node; tests/test_nl_laptop_quality.py calls it.   node tests/js/nl_laptop_quality_check.js
"use strict";
const fs = require("fs");
const path = require("path");

let failed = 0;
function check(label, ok, detail) {
  if (ok) { console.log("  ok  " + label); } else { failed += 1; console.log("  FAIL " + label + (detail !== undefined ? " -> " + JSON.stringify(detail) : "")); }
}
const root = path.join(__dirname, "..", "..", "static", "nl");
const X = require(path.join(root, "player-extras.js"));
const caps = "v=h264.hevc;a=aac.flac;h=h264.hevc.aac.flac";
const enc = encodeURIComponent(caps);
const q = (o) => X.playDeviceParams(Object.assign({ casting: false, isNative: false, isLaptop: false, allowConversion: false, caps: caps }, o));

check("laptop: client=laptop and caps", JSON.stringify(q({ isLaptop: true })) === JSON.stringify(["client=laptop", "caps=" + enc]), q({ isLaptop: true }));
check("laptop after the button: allow_conversion=1", JSON.stringify(q({ isLaptop: true, allowConversion: true })) === JSON.stringify(["client=laptop", "caps=" + enc, "allow_conversion=1"]), q({ isLaptop: true, allowConversion: true }));
check("laptop while casting: nothing (the cast endpoint picks the profile)", q({ isLaptop: true, casting: true }).length === 0);
check("TV app: client=tv only, no caps", JSON.stringify(q({ isNative: true })) === JSON.stringify(["client=tv"]), q({ isNative: true }));
check("TV app after the button: allow_conversion=1", JSON.stringify(q({ isNative: true, allowConversion: true })) === JSON.stringify(["client=tv", "allow_conversion=1"]));
check("phone / browser: caps only, never allow_conversion", JSON.stringify(q({})) === JSON.stringify(["caps=" + enc]) && q({ allowConversion: true }).indexOf("allow_conversion=1") < 0, q({ allowConversion: true }));

const js = fs.readFileSync(path.join(root, "player.js"), "utf8");
check("fetchPlay builds the device part with playDeviceParams", /NLPlayerExtras\.playDeviceParams\(\{[\s\S]{0,200}isLaptop: isLaptop[\s\S]{0,120}allowConversion: S\.allowConversion/.test(js));
check("no other place adds allow_conversion", (js.match(/allow_conversion/g) || []).length <= 1 && js.indexOf('isNative && S.allowConversion') < 0);
check("the convert button shows for the TV and the laptop", js.indexOf('$("nl-convert").hidden = !(canConvert && (isNative || isLaptop))') >= 0);
check("a picture subtitle on the laptop and the TV asks first, then sets allowConversion and switches",
  /device: isNative \? "tv" : \(isLaptop \? "laptop" : "browser"\)/.test(js) &&
  /track\.kind === "image" && pictureStep\(index\) === "ask"\)[\s\S]{0,900}Play a converted copy[\s\S]{0,200}S\.allowConversion = true;\s*chooseSubtitle\(index\)[\s\S]{0,200}Keep the original/.test(js));
check("the question carries the plain sentence", js.indexOf("This subtitle is a picture. Showing it needs a converted copy (lower quality).") >= 0);
check("the next episode forgets the agreement (as the TV does)", /opts\.fromNext\) \{ S\.allowConversion = false;/.test(js));
check("the old burn-in branch is still there for the phone", /track\.kind === "image"\) \{\s*S\.textIndex = null;[\s\S]{0,200}reloadStream\(\{ subtitle: index \}\)/.test(js));

if (failed) { console.log(failed + " failed"); process.exit(1); }
console.log("all laptop quality checks passed");
