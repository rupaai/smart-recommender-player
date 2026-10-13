/* A small QR code encoder (M8b), written for this app so nothing is loaded from a CDN.
 *
 *   NLQR.encode("http://192.168.1.50:8000/nl/type/K7M2QD") -> { version, size, modules: [[bool]] }   (true = dark)
 *   NLQR.toSvg(text, { margin: 4, dark: "#14121f", light: "#ffffff" }) -> "<svg ...>" string (one path)
 *   NLQR.toElement(text, options) -> an <svg> element (built with createElementNS, no innerHTML)
 *
 * Scope: byte mode (UTF-8), error correction level M, versions 1 to 10 (up to 213 bytes), which is plenty for a
 * short address. Longer text throws. The structure follows ISO/IEC 18004 (finder, alignment and timing patterns,
 * format and version information, Reed-Solomon over GF(256) with 0x11D, the 8 masks scored by the four penalty rules).
 */
(function (global) {
  "use strict";

  // Level M, versions 1..10: [error-correction codewords per block, [[blocks, data codewords per block], ...]]
  var BLOCKS = {
    1: [10, [[1, 16]]], 2: [16, [[1, 28]]], 3: [26, [[1, 44]]], 4: [18, [[2, 32]]], 5: [24, [[2, 43]]],
    6: [16, [[4, 27]]], 7: [18, [[4, 31]]], 8: [22, [[2, 38], [2, 39]]], 9: [22, [[3, 36], [2, 37]]], 10: [26, [[4, 43], [1, 44]]]
  };
  var ALIGN = { 1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30], 6: [6, 34], 7: [6, 22, 38], 8: [6, 24, 42], 9: [6, 26, 46], 10: [6, 28, 50] };
  var REMAINDER_BITS = { 1: 0, 2: 7, 3: 7, 4: 7, 5: 7, 6: 7, 7: 0, 8: 0, 9: 0, 10: 0 };
  var MAX_VERSION = 10;

  function dataCodewords(version) {
    return BLOCKS[version][1].reduce(function (sum, g) { return sum + g[0] * g[1]; }, 0);
  }
  function capacityBytes(version) {
    var countBits = version < 10 ? 8 : 16;
    return Math.floor((dataCodewords(version) * 8 - 4 - countBits) / 8);
  }

  /* ----------------------------------------------------------- Reed-Solomon over GF(256) */

  function gfMul(x, y) {
    var z = 0;
    for (var i = 7; i >= 0; i--) {
      z = (z << 1) ^ ((z >>> 7) * 0x11D);
      z ^= ((y >>> i) & 1) * x;
    }
    return z & 0xFF;
  }
  function rsDivisor(degree) {
    var result = [];
    for (var i = 0; i < degree - 1; i++) { result.push(0); }
    result.push(1);
    var root = 1;
    for (var d = 0; d < degree; d++) {
      for (var j = 0; j < result.length; j++) {
        result[j] = gfMul(result[j], root);
        if (j + 1 < result.length) { result[j] ^= result[j + 1]; }
      }
      root = gfMul(root, 0x02);
    }
    return result;
  }
  function rsRemainder(data, divisor) {
    var result = divisor.map(function () { return 0; });
    data.forEach(function (b) {
      var factor = b ^ result.shift();
      result.push(0);
      divisor.forEach(function (coef, i) { result[i] ^= gfMul(coef, factor); });
    });
    return result;
  }

  /* ----------------------------------------------------------- data codewords */

  function utf8(text) {
    var out = [];
    var s = unescape(encodeURIComponent(text));
    for (var i = 0; i < s.length; i++) { out.push(s.charCodeAt(i)); }
    return out;
  }

  function pushBits(bits, value, length) {
    for (var i = length - 1; i >= 0; i--) { bits.push((value >>> i) & 1); }
  }

  function makeCodewords(bytes, version) {
    var capacity = dataCodewords(version) * 8;
    var bits = [];
    pushBits(bits, 0x4, 4);                                   // byte mode
    pushBits(bits, bytes.length, version < 10 ? 8 : 16);
    bytes.forEach(function (b) { pushBits(bits, b, 8); });
    pushBits(bits, 0, Math.min(4, capacity - bits.length));   // terminator
    while (bits.length % 8 !== 0) { bits.push(0); }
    var pad = 0xEC;
    while (bits.length < capacity) { pushBits(bits, pad, 8); pad ^= 0xEC ^ 0x11; }
    var words = [];
    for (var i = 0; i < bits.length; i += 8) {
      var w = 0;
      for (var j = 0; j < 8; j++) { w = (w << 1) | bits[i + j]; }
      words.push(w);
    }
    return words;
  }

  /** Splits into blocks, adds the error correction words and interleaves (ISO 18004 section 7.6). */
  function interleave(words, version) {
    var ecLen = BLOCKS[version][0];
    var blocks = [];
    var offset = 0;
    BLOCKS[version][1].forEach(function (group) {
      for (var n = 0; n < group[0]; n++) {
        blocks.push(words.slice(offset, offset + group[1]));
        offset += group[1];
      }
    });
    var divisor = rsDivisor(ecLen);
    var ecs = blocks.map(function (block) { return rsRemainder(block, divisor); });
    var out = [];
    var maxData = Math.max.apply(null, blocks.map(function (b) { return b.length; }));
    for (var i = 0; i < maxData; i++) {
      blocks.forEach(function (b) { if (i < b.length) { out.push(b[i]); } });
    }
    for (var k = 0; k < ecLen; k++) {
      ecs.forEach(function (e) { out.push(e[k]); });
    }
    return out;
  }

  /* ----------------------------------------------------------- the matrix */

  function bit(value, i) { return ((value >>> i) & 1) !== 0; }

  function newGrid(size, fill) {
    var g = [];
    for (var y = 0; y < size; y++) {
      var row = [];
      for (var x = 0; x < size; x++) { row.push(fill); }
      g.push(row);
    }
    return g;
  }

  function Matrix(version) {
    this.version = version;
    this.size = version * 4 + 17;
    this.modules = newGrid(this.size, false);
    this.isFunction = newGrid(this.size, false);
    this.drawFunctionPatterns();
  }
  Matrix.prototype.setFunction = function (x, y, dark) {
    this.modules[y][x] = dark;
    this.isFunction[y][x] = true;
  };
  Matrix.prototype.drawFunctionPatterns = function () {
    var size = this.size, i;
    for (i = 0; i < size; i++) {
      this.setFunction(6, i, i % 2 === 0);
      this.setFunction(i, 6, i % 2 === 0);
    }
    this.drawFinder(3, 3);
    this.drawFinder(size - 4, 3);
    this.drawFinder(3, size - 4);
    var pos = ALIGN[this.version];
    for (var a = 0; a < pos.length; a++) {
      for (var b = 0; b < pos.length; b++) {
        if ((a === 0 && b === 0) || (a === 0 && b === pos.length - 1) || (a === pos.length - 1 && b === 0)) { continue; }
        this.drawAlignment(pos[a], pos[b]);
      }
    }
    this.drawFormat(0);   // reserves the cells; the real mask is written later
    this.drawVersion();
  };
  Matrix.prototype.drawFinder = function (cx, cy) {
    for (var dy = -4; dy <= 4; dy++) {
      for (var dx = -4; dx <= 4; dx++) {
        var dist = Math.max(Math.abs(dx), Math.abs(dy));
        var x = cx + dx, y = cy + dy;
        if (x >= 0 && x < this.size && y >= 0 && y < this.size) { this.setFunction(x, y, dist !== 2 && dist !== 4); }
      }
    }
  };
  Matrix.prototype.drawAlignment = function (cx, cy) {
    for (var dy = -2; dy <= 2; dy++) {
      for (var dx = -2; dx <= 2; dx++) { this.setFunction(cx + dx, cy + dy, Math.max(Math.abs(dx), Math.abs(dy)) !== 1); }
    }
  };
  Matrix.prototype.drawFormat = function (mask) {
    var data = (0 << 3) | mask;                // error correction level M has format bits 00
    var rem = data, i;
    for (i = 0; i < 10; i++) { rem = (rem << 1) ^ ((rem >>> 9) * 0x537); }
    var bits = ((data << 10) | rem) ^ 0x5412;
    var size = this.size;
    for (i = 0; i <= 5; i++) { this.setFunction(8, i, bit(bits, i)); }
    this.setFunction(8, 7, bit(bits, 6));
    this.setFunction(8, 8, bit(bits, 7));
    this.setFunction(7, 8, bit(bits, 8));
    for (i = 9; i < 15; i++) { this.setFunction(14 - i, 8, bit(bits, i)); }
    for (i = 0; i < 8; i++) { this.setFunction(size - 1 - i, 8, bit(bits, i)); }
    for (i = 8; i < 15; i++) { this.setFunction(8, size - 15 + i, bit(bits, i)); }
    this.setFunction(8, size - 8, true);       // the always-dark module
  };
  Matrix.prototype.drawVersion = function () {
    if (this.version < 7) { return; }
    var rem = this.version, i;
    for (i = 0; i < 12; i++) { rem = (rem << 1) ^ ((rem >>> 11) * 0x1F25); }
    var bits = (this.version << 12) | rem;
    for (i = 0; i < 18; i++) {
      var a = this.size - 11 + (i % 3), b = Math.floor(i / 3);
      this.setFunction(a, b, bit(bits, i));
      this.setFunction(b, a, bit(bits, i));
    }
  };
  Matrix.prototype.drawCodewords = function (words) {
    var size = this.size, i = 0, total = words.length * 8;
    for (var right = size - 1; right >= 1; right -= 2) {
      if (right === 6) { right = 5; }
      for (var vert = 0; vert < size; vert++) {
        for (var j = 0; j < 2; j++) {
          var x = right - j;
          var upward = ((right + 1) & 2) === 0;
          var y = upward ? size - 1 - vert : vert;
          if (!this.isFunction[y][x] && i < total) {
            this.modules[y][x] = bit(words[i >>> 3], 7 - (i & 7));
            i++;
          }
        }
      }
    }
  };
  Matrix.prototype.applyMask = function (mask) {
    for (var y = 0; y < this.size; y++) {
      for (var x = 0; x < this.size; x++) {
        var invert;
        switch (mask) {
          case 0: invert = (x + y) % 2 === 0; break;
          case 1: invert = y % 2 === 0; break;
          case 2: invert = x % 3 === 0; break;
          case 3: invert = (x + y) % 3 === 0; break;
          case 4: invert = (Math.floor(x / 3) + Math.floor(y / 2)) % 2 === 0; break;
          case 5: invert = (x * y) % 2 + (x * y) % 3 === 0; break;
          case 6: invert = ((x * y) % 2 + (x * y) % 3) % 2 === 0; break;
          default: invert = ((x + y) % 2 + (x * y) % 3) % 2 === 0; break;
        }
        if (!this.isFunction[y][x] && invert) { this.modules[y][x] = !this.modules[y][x]; }
      }
    }
  };
  // The four penalty rules (ISO 18004 section 7.8.3): runs of 5+, 2x2 blocks, finder-like patterns, dark/light balance.
  Matrix.prototype.penalty = function () {
    var size = this.size, m = this.modules, score = 0, x, y;
    var PATTERNS = ["10111010000", "00001011101"];
    function scanLine(cells) {
      var s = 0, run = 1, i;
      for (i = 1; i < cells.length; i++) {
        if (cells[i] === cells[i - 1]) {
          run++;
          if (run === 5) { s += 3; } else if (run > 5) { s += 1; }
        } else { run = 1; }
      }
      var text = cells.map(function (c) { return c ? "1" : "0"; }).join("");
      PATTERNS.forEach(function (pat) {
        for (var at = text.indexOf(pat); at >= 0; at = text.indexOf(pat, at + 1)) { s += 40; }
      });
      return s;
    }
    for (y = 0; y < size; y++) { score += scanLine(m[y]); }
    for (x = 0; x < size; x++) {
      var col = [];
      for (y = 0; y < size; y++) { col.push(m[y][x]); }
      score += scanLine(col);
    }
    var dark = 0;
    for (y = 0; y < size; y++) {
      for (x = 0; x < size; x++) {
        if (m[y][x]) { dark++; }
        if (x < size - 1 && y < size - 1 && m[y][x] === m[y][x + 1] && m[y][x] === m[y + 1][x] && m[y][x] === m[y + 1][x + 1]) { score += 3; }
      }
    }
    var total = size * size;
    score += Math.max(0, Math.ceil(Math.abs(dark * 20 - total * 10) / total) - 1) * 10;
    return score;
  };

  function encode(text) {
    var bytes = utf8(String(text));
    var version = 0;
    for (var v = 1; v <= MAX_VERSION; v++) {
      if (bytes.length <= capacityBytes(v)) { version = v; break; }
    }
    if (!version) { throw new Error("Text too long for this QR encoder (" + bytes.length + " bytes, max " + capacityBytes(MAX_VERSION) + ")."); }
    var words = interleave(makeCodewords(bytes, version), version);
    var best = null, bestScore = Infinity, bestMask = 0;
    for (var mask = 0; mask < 8; mask++) {
      var m = new Matrix(version);
      m.drawCodewords(words);
      m.applyMask(mask);
      m.drawFormat(mask);
      var score = m.penalty();
      if (score < bestScore) { best = m; bestScore = score; bestMask = mask; }
    }
    return { version: version, size: best.size, mask: bestMask, modules: best.modules, isFunction: best.isFunction, codewords: words };
  }

  function toSvg(text, options) {
    options = options || {};
    var margin = options.margin === undefined ? 4 : options.margin;
    var qr = encode(text);
    var dim = qr.size + margin * 2;
    var d = [];
    for (var y = 0; y < qr.size; y++) {
      for (var x = 0; x < qr.size; x++) {
        if (qr.modules[y][x]) { d.push("M" + (x + margin) + "," + (y + margin) + "h1v1h-1z"); }
      }
    }
    return '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ' + dim + " " + dim + '" shape-rendering="crispEdges" role="img">' +
      '<rect width="' + dim + '" height="' + dim + '" fill="' + (options.light || "#ffffff") + '"/>' +
      '<path d="' + d.join("") + '" fill="' + (options.dark || "#000000") + '"/></svg>';
  }

  function toElement(text, options) {
    options = options || {};
    var margin = options.margin === undefined ? 4 : options.margin;
    var qr = encode(text);
    var dim = qr.size + margin * 2;
    var NS = "http://www.w3.org/2000/svg";
    var svg = global.document.createElementNS(NS, "svg");
    svg.setAttribute("viewBox", "0 0 " + dim + " " + dim);
    svg.setAttribute("shape-rendering", "crispEdges");
    svg.setAttribute("role", "img");
    var bg = global.document.createElementNS(NS, "rect");
    bg.setAttribute("width", String(dim));
    bg.setAttribute("height", String(dim));
    bg.setAttribute("fill", options.light || "#ffffff");
    svg.appendChild(bg);
    var d = [];
    for (var y = 0; y < qr.size; y++) {
      for (var x = 0; x < qr.size; x++) {
        if (qr.modules[y][x]) { d.push("M" + (x + margin) + "," + (y + margin) + "h1v1h-1z"); }
      }
    }
    var path = global.document.createElementNS(NS, "path");
    path.setAttribute("d", d.join(""));
    path.setAttribute("fill", options.dark || "#000000");
    svg.appendChild(path);
    return svg;
  }

  global.NLQR = { encode: encode, toSvg: toSvg, toElement: toElement, capacityBytes: capacityBytes, MAX_VERSION: MAX_VERSION };
})(typeof window !== "undefined" ? window : this);
