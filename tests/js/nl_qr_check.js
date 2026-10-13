// Behaviour check for static/nl/qr.js (M8b): the QR encoder. Plain node; tests/test_nl_type.py calls it when node exists.
//   node tests/js/nl_qr_check.js
// It decodes what the encoder made with its own small decoder (own GF(256) tables, own block table): format bits and
// their BCH check, unmasking, de-interleaving, Reed-Solomon syndromes, and the text back out. (The same matrices were
// also read by an independent decoder, zxing-cpp, for every version and size boundary when this was written.)
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

let failed = 0;
function check(label, ok, detail) {
  if (ok) { console.log("  ok  " + label); } else { failed += 1; console.log("  FAIL " + label + (detail !== undefined ? " -> " + JSON.stringify(detail) : "")); }
}

const src = fs.readFileSync(path.join(__dirname, "..", "..", "static", "nl", "qr.js"), "utf8");
const win = {};
vm.createContext(win);
win.window = win;
vm.runInContext(src, win);
const NLQR = win.NLQR;

// ---- an independent decoder for what the encoder claims to make (level M, byte mode) ----
const BLOCKS = { // [ec per block, [[count, data per block]]], from the ISO 18004 table, typed again here
  1: [10, [[1, 16]]], 2: [16, [[1, 28]]], 3: [26, [[1, 44]]], 4: [18, [[2, 32]]], 5: [24, [[2, 43]]], 6: [16, [[4, 27]]],
  7: [18, [[4, 31]]], 8: [22, [[2, 38], [2, 39]]], 9: [22, [[3, 36], [2, 37]]], 10: [26, [[4, 43], [1, 44]]]
};
const EXP = new Array(512), LOG = new Array(256);
(function () { let x = 1; for (let i = 0; i < 255; i++) { EXP[i] = x; LOG[x] = i; x <<= 1; if (x & 0x100) { x ^= 0x11d; } } for (let i = 255; i < 512; i++) { EXP[i] = EXP[i - 255]; } })();
const mul = (a, b) => (a === 0 || b === 0 ? 0 : EXP[LOG[a] + LOG[b]]);

function bch(value, bitsIn, gen, bitsOut) { let r = value << bitsOut; for (let i = bitsIn + bitsOut - 1; i >= bitsOut; i--) { if ((r >>> i) & 1) { r ^= gen << (i - bitsOut); } } return (value << bitsOut) | r; }

function readFormat(m) {
  const n = m.length;
  const copy1 = [], copy2 = [];
  for (let i = 0; i <= 5; i++) { copy1[i] = m[i][8]; }
  copy1[6] = m[7][8]; copy1[7] = m[8][8]; copy1[8] = m[8][7];
  for (let i = 9; i < 15; i++) { copy1[i] = m[8][14 - i]; }
  for (let i = 0; i < 8; i++) { copy2[i] = m[8][n - 1 - i]; }
  for (let i = 8; i < 15; i++) { copy2[i] = m[n - 15 + i][8]; }
  const toInt = (a) => a.reduce((acc, b, i) => acc | ((b ? 1 : 0) << i), 0);
  return [toInt(copy1), toInt(copy2)];
}

function decode(qr) {
  const m = qr.modules.map((r) => r.slice());
  const [f1, f2] = readFormat(m);
  const format = f1 ^ 0x5412;
  const data5 = format >>> 10;
  if (f1 !== f2 || bch(data5, 5, 0x537, 10) !== format) { return { error: "format bits wrong", f1, f2 }; }
  if ((data5 >>> 3) !== 0) { return { error: "not level M" }; }
  const mask = data5 & 7;
  const n = m.length;
  // unmask the data region and read it in the zigzag order
  const bits = [];
  for (let right = n - 1; right >= 1; right -= 2) {
    if (right === 6) { right = 5; }
    for (let vert = 0; vert < n; vert++) {
      for (let j = 0; j < 2; j++) {
        const x = right - j, upward = ((right + 1) & 2) === 0, y = upward ? n - 1 - vert : vert;
        if (qr.isFunction[y][x]) { continue; }
        const inv = [(x + y) % 2 === 0, y % 2 === 0, x % 3 === 0, (x + y) % 3 === 0, (Math.floor(x / 3) + Math.floor(y / 2)) % 2 === 0,
          (x * y) % 2 + (x * y) % 3 === 0, ((x * y) % 2 + (x * y) % 3) % 2 === 0, ((x + y) % 2 + (x * y) % 3) % 2 === 0][mask];
        bits.push(m[y][x] !== inv ? 1 : 0);
      }
    }
  }
  const words = [];
  for (let i = 0; i + 8 <= bits.length; i += 8) { words.push(bits.slice(i, i + 8).reduce((a, b) => (a << 1) | b, 0)); }
  const [ec, groups] = BLOCKS[qr.version];
  const sizes = [];
  groups.forEach(([count, data]) => { for (let i = 0; i < count; i++) { sizes.push(data); } });
  const blocks = sizes.map(() => []);
  const maxData = Math.max(...sizes);
  let p = 0;
  for (let i = 0; i < maxData; i++) { sizes.forEach((sz, b) => { if (i < sz) { blocks[b].push(words[p++]); } }); }
  const ecs = sizes.map(() => []);
  for (let i = 0; i < ec; i++) { sizes.forEach((sz, b) => { ecs[b].push(words[p++]); }); }
  // Reed-Solomon: every block's codeword polynomial must vanish at alpha^0 .. alpha^(ec-1)
  for (let b = 0; b < blocks.length; b++) {
    const cw = blocks[b].concat(ecs[b]);
    for (let r = 0; r < ec; r++) {
      let acc = 0;
      for (const c of cw) { acc = mul(acc, EXP[r]) ^ c; }
      if (acc !== 0) { return { error: "RS syndrome " + r + " of block " + b + " is not zero" }; }
    }
  }
  const data = [].concat(...blocks);
  const stream = data.flatMap((w) => [7, 6, 5, 4, 3, 2, 1, 0].map((i) => (w >>> i) & 1));
  const take = (k) => { let v = 0; for (let i = 0; i < k; i++) { v = (v << 1) | stream.shift(); } return v; };
  if (take(4) !== 4) { return { error: "not byte mode" }; }
  const len = take(qr.version < 10 ? 8 : 16);
  const bytes = [];
  for (let i = 0; i < len; i++) { bytes.push(take(8)); }
  return { text: Buffer.from(bytes).toString("utf8") };
}

console.log("encoding and decoding again");
const samples = [
  "A", "hello", "http://192.168.1.50:8000/nl/type/K7M2QD", "https://mars.tail1234.ts.net/nl/type/K7M2QD",
  "Zażółć gęślą jaźń ☃ \u{1F600}", "x".repeat(14), "x".repeat(15), "x".repeat(26), "x".repeat(27), "x".repeat(42), "x".repeat(43),
  "x".repeat(62), "x".repeat(63), "x".repeat(84), "x".repeat(85), "x".repeat(106), "x".repeat(107), "x".repeat(122), "x".repeat(123),
  "x".repeat(152), "x".repeat(153), "x".repeat(180), "x".repeat(181), "x".repeat(213)
];
for (let n = 1; n <= 200; n += 9) { samples.push(Array.from({ length: n }, (_, i) => String.fromCharCode(33 + ((i * 11 + n) % 90))).join("")); }
let allOk = true;
const versionsSeen = new Set();
samples.forEach((t) => {
  const qr = NLQR.encode(t);
  versionsSeen.add(qr.version);
  const out = decode(qr);
  if (out.text !== t) { allOk = false; check("round trip " + JSON.stringify(t.slice(0, 20)) + " v" + qr.version, false, out); }
});
check("every sample decodes back to its text (" + samples.length + " samples)", allOk);
check("all versions 1 to 10 were exercised", versionsSeen.size === 10, Array.from(versionsSeen).sort());

console.log("structure");
const q = NLQR.encode("http://192.168.1.50:8000/nl/type/K7M2QD");
check("version 3 for a 39-byte address, size 29", q.version === 3 && q.size === 29 && q.modules.length === 29 && q.modules.every((r) => r.length === 29), [q.version, q.size]);
const finder = (ox, oy) => {
  for (let y = 0; y < 7; y++) { for (let x = 0; x < 7; x++) {
    const ring = Math.max(Math.abs(x - 3), Math.abs(y - 3));
    if (q.modules[oy + y][ox + x] !== (ring !== 2)) { return false; }
  } }
  return true;
};
check("three finder patterns", finder(0, 0) && finder(q.size - 7, 0) && finder(0, q.size - 7));
let timing = true;
for (let i = 8; i < q.size - 8; i++) { if (q.modules[6][i] !== (i % 2 === 0) || q.modules[i][6] !== (i % 2 === 0)) { timing = false; } }
check("timing patterns alternate", timing);
check("the always-dark module is dark", q.modules[q.size - 8][8] === true);
check("alignment pattern for version 3 is centred at (22, 22)", q.modules[22][22] === true && q.modules[22][21] === false && q.modules[20][20] === true);
check("deterministic", JSON.stringify(NLQR.encode("same").modules) === JSON.stringify(NLQR.encode("same").modules));
check("different text, different picture", JSON.stringify(NLQR.encode("one").modules) !== JSON.stringify(NLQR.encode("two").modules));
check("capacity table", NLQR.capacityBytes(1) === 14 && NLQR.capacityBytes(4) === 62 && NLQR.capacityBytes(10) === 213);
let threw = false;
try { NLQR.encode("x".repeat(214)); } catch (e) { threw = /too long/i.test(e.message); }
check("text over 213 bytes is refused, not mangled", threw);

console.log("SVG");
const svg = NLQR.toSvg("http://tv/nl/type/K7M2QD", { dark: "#14121f", light: "#ffffff", margin: 2 });
check("an svg with a viewBox, a light background and one dark path", svg.startsWith("<svg ") && /viewBox="0 0 \d+ \d+"/.test(svg) && (svg.match(/<path /g) || []).length === 1 && svg.includes('fill="#14121f"'), svg.slice(0, 120));
const dim = Number((svg.match(/viewBox="0 0 (\d+)/) || [])[1]);
check("quiet zone of 2 modules each side", dim === NLQR.encode("http://tv/nl/type/K7M2QD").size + 4, dim);
check("no script or event handler can get into the svg", !/<script|onload|onerror|javascript:/i.test(svg));

if (failed) { console.log("\n" + failed + " FAILED"); process.exit(1); }
console.log("\nall qr.js checks passed");
