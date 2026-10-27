// Behaviour check for the laptop player's input split: NLPlayerExtras.inputMode (big-screen look vs remote input),
// the stylesheet's laptop overrides, and that player.js routes keys / cast / pointer by those flags.
// Plain node; tests/test_nl_laptop.py calls it.   node tests/js/nl_laptop_controls_check.js
"use strict";
const fs = require("fs");
const path = require("path");

let failed = 0;
function check(label, ok, detail) {
  if (ok) { console.log("  ok  " + label); } else { failed += 1; console.log("  FAIL " + label + (detail !== undefined ? " -> " + JSON.stringify(detail) : "")); }
}
const root = path.join(__dirname, "..", "..", "static", "nl");
const X = require(path.join(root, "player-extras.js"));

const laptop = X.inputMode("laptop", false, false);
check("laptop: big screen look, no remote, cast on, web keys", laptop.bigScreen && !laptop.remote && laptop.cast && laptop.keys === "web" && laptop.laptop, laptop);
const laptopTvParam = X.inputMode("laptop", false, true);
check("laptop with ?tv=1 is still the remote", laptopTvParam.remote && !laptopTvParam.cast && laptopTvParam.keys === "remote", laptopTvParam);
const tvParam = X.inputMode("web", false, true);
check("?tv=1: big screen, remote keys, cast off", tvParam.bigScreen && tvParam.remote && !tvParam.cast && tvParam.keys === "remote" && !tvParam.laptop, tvParam);
const native = X.inputMode("web", true, false);
check("Android app: big screen, remote keys, cast off", native.bigScreen && native.remote && !native.cast && native.keys === "remote", native);
const web = X.inputMode("web", false, false);
check("web: normal look, web keys, cast on", !web.bigScreen && !web.remote && web.cast && web.keys === "web", web);

// player.js wiring (it is too big to run in a vm: it needs the whole page, hls.js and the Jellyfin API).
const js = fs.readFileSync(path.join(root, "player.js"), "utf8");
check("player.js has no leftover isTv", !/\bisTv\b/.test(js));
check("cast is enabled from the mode (laptop true, remote false)", js.indexOf("NLCast.attach(window, localPlayer, mode.cast)") >= 0);
check("TV remote key handlers only run for the remote", js.indexOf("if (remote && mediaKey(e))") >= 0 && js.indexOf("if (remote && tvKey(e))") >= 0);
check("web key handler has the shortcuts (Space/K, J/L, M, F, arrows)",
  /case " ": case "k": case "K": togglePlay/.test(js) && /case "m": case "M": toggleMute/.test(js) && /case "f": case "F": player\.toggleFullscreen/.test(js)
  && /case "ArrowLeft": case "j": case "J": skipBy\(-1\)/.test(js) && /case "ArrowUp": nudgeVolume/.test(js));
check("pointer move shows the controls (not gated by the remote)", /pointermove[\s\S]{0,200}showControls\(\)/.test(js) && !/pointermove[^\n]*remote/.test(js));
check("click toggles play and double-click is fullscreen", /root\.addEventListener\("click"[\s\S]{0,300}togglePlay\(\)/.test(js) && /dblclick[\s\S]{0,200}toggleFullscreen\(root\)/.test(js));
check("Escape leaves on the big screen when not full screen", /bigScreen && !document\.fullscreenElement[^\n]*leave\(\)/.test(js));
check("touch gestures are off only for the remote", js.indexOf('S.pointerType === "touch" && !remote') >= 0);

// player.css: the TV hides these (no pointer); the laptop block comes later and brings them back.
const css = fs.readFileSync(path.join(root, "player.css"), "utf8");
const hide = css.indexOf(".tv .nl-center { display: none; }");
const hide2 = css.indexOf(".tv #nl-mute, .tv #nl-volume, .tv #nl-fs { display: none; }");
const back1 = css.indexOf(".tv.laptop .nl-center, .tv.laptop #nl-mute, .tv.laptop #nl-fs { display: flex; }");
const back2 = css.indexOf(".tv.laptop #nl-volume { display: block; }");
check("TV still hides the centre button, mute, volume and fullscreen", hide >= 0 && hide2 >= 0);
check("laptop overrides come after the hide rules", back1 > hide && back1 > hide2 && back2 > hide2, [hide, hide2, back1, back2]);
check("laptop centre button and volume are sized for the big screen", /\.tv\.laptop \.nl-center \{ width: 7rem/.test(css) && /\.tv\.laptop \.nl-volume \{ width: 11rem/.test(css));
check("a mouse click does not get the remote's focus glow on the laptop", css.indexOf(".tv.laptop .nl-btn:focus:not(:focus-visible)") > hide);

process.exit(failed ? 1 : 0);
