// Renders static/nl/tv/aurora-sky.webp, the still aurora behind the top of every TV page (and the TV player's
// full-screen questions): curtains of thin rays along wavy lower edges, blurred, faded into the night colour (#070b15).
// The sky is drawn here once so the TV only decodes a ~24 KB picture instead of painting filters on every page.
//
//   NODE_PATH=$(npm root -g) node scripts/render_aurora_sky.js /tmp/sky.png
//   ffmpeg -i /tmp/sky.png -c:v libwebp -quality 90 -compression_level 6 static/nl/tv/aurora-sky.webp
const { chromium } = require('playwright');
const W = 1920, H = 760, BG = '#070b15';
let seed = 7; const rnd = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
function noise1(n) { const v = []; let a = 0, b = 0; for (let i = 0; i < n; i++) { a += (rnd() - 0.5) * 0.35; a *= 0.97; b = b * 0.8 + a * 0.2; v.push(b); } return v; }
function curtain(id, edge, top, bottom, len, step, op, x0 = 0, x1 = W) {
  const n = noise1(Math.ceil((x1 - x0) / step) + 2), m = noise1(n.length);
  let s = `<g filter="url(#blur)">`;
  for (let i = 0, x = x0; x < x1; x += step, i++) {
    const y = edge(x) + (rnd() - 0.5) * 10;
    const h = len * (0.7 + 0.6 * Math.abs(m[i]) + 0.25 * rnd());
    const a = Math.max(0, Math.min(1, op * (0.42 + Math.max(-0.3, n[i] * 3.0) + 0.18 * rnd())));
    s += `<rect x="${x.toFixed(1)}" y="${(y - h).toFixed(1)}" width="${(step * 0.9).toFixed(1)}" height="${h.toFixed(1)}" fill="url(#${id})" opacity="${a.toFixed(3)}"/>`;
  }
  let rim = 'M' + x0 + ' ' + edge(x0).toFixed(1); for (let x = x0; x <= x1; x += 8) rim += ' L' + x + ' ' + edge(x).toFixed(1);
  s += `</g><path d="${rim}" fill="none" stroke="${bottom}" stroke-width="6" stroke-opacity="${(op*0.45).toFixed(2)}" filter="url(#rim)"/>`;
  return s +
    `<defs><linearGradient id="${id}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="${top}" stop-opacity="0"/><stop offset="0.55" stop-color="${top}" stop-opacity="0.55"/><stop offset="0.9" stop-color="${bottom}" stop-opacity="0.95"/><stop offset="1" stop-color="${bottom}" stop-opacity="0.2"/></linearGradient></defs>`;
}
const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}">
<defs><filter id="blur" x="-5%" y="-20%" width="110%" height="140%"><feGaussianBlur stdDeviation="6 16"/></filter>
<filter id="rim" x="-5%" y="-50%" width="110%" height="200%"><feGaussianBlur stdDeviation="6 10"/></filter><filter id="soft"><feGaussianBlur stdDeviation="60"/></filter>
<linearGradient id="fade" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="${BG}" stop-opacity="0"/><stop offset="0.45" stop-color="${BG}" stop-opacity="0"/><stop offset="1" stop-color="${BG}" stop-opacity="1"/></linearGradient>
<radialGradient id="glowG" cx="0.3" cy="0.35" r="0.5"><stop offset="0" stop-color="#1fb87f" stop-opacity="0.35"/><stop offset="1" stop-color="#1fb87f" stop-opacity="0"/></radialGradient>
<radialGradient id="glowV" cx="0.78" cy="0.2" r="0.45"><stop offset="0" stop-color="#6a4fe0" stop-opacity="0.32"/><stop offset="1" stop-color="#6a4fe0" stop-opacity="0"/></radialGradient>
</defs>
<rect width="${W}" height="${H}" fill="${BG}"/>
<rect width="${W}" height="${H}" fill="url(#glowG)"/>
<rect width="${W}" height="${H}" fill="url(#glowV)"/>
${curtain('cv', x => 230 + 34 * Math.sin(x / 95 + 1.2) + 22 * Math.sin(x / 41), '#ff6fae', '#8f72ff', 200, 4, 0.5, 1020, W)}
${curtain('ct', x => 300 + 46 * Math.sin(x / 140) + 20 * Math.sin(x / 57 + 2), '#8f72ff', '#2fd3c9', 240, 3, 0.55, 0, 1450)}
${curtain('cg', x => 390 + 52 * Math.sin(x / 175 + 0.4) + 26 * Math.sin(x / 66 + 2.2) + 9 * Math.sin(x / 23), '#2fd3c9', '#46f0a6', 290, 3, 0.85, 40, 1390)}
${curtain('cg2', x => 345 + 40 * Math.sin(x / 120 + 3) + 16 * Math.sin(x / 47), '#2fd3c9', '#46f0a6', 230, 3, 0.6, 1180, 1900)}
<rect width="${W}" height="${H}" fill="url(#fade)"/>
</svg>`;
const OUT = process.argv[2] || 'aurora-sky.png';
(async () => {
  const b = await chromium.launch(); const p = await b.newPage({ viewport: { width: W, height: H } });
  await p.setContent(`<html><body style="margin:0;background:${BG}">${svg}</body></html>`);
  await p.waitForTimeout(500);
  await p.screenshot({ path: OUT });
  await b.close();
})();
