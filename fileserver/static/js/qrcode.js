/*
 * Minimal QR Code encoder (byte mode, error-correction level L, versions 1-9).
 * Self-contained, no dependencies. Implements the ISO/IEC 18004 pipeline:
 * data encoding -> Reed-Solomon ECC -> matrix layout -> mask selection.
 *
 * Usage:
 *   QRCode.encode("text")        -> { size, modules, mask }
 *   QRCode.toSvg("text", opts)   -> SVG markup string
 *   QRCode.toCanvas("text", el)  -> draws onto a <canvas>
 */
(function (global) {
  "use strict";

  // --- GF(256) arithmetic (primitive polynomial 0x11D) --------------------
  var EXP = new Uint8Array(256);
  var LOG = new Uint8Array(256);
  (function () {
    var x = 1;
    for (var i = 0; i < 255; i++) {
      EXP[i] = x;
      LOG[x] = i;
      x <<= 1;
      if (x & 0x100) x ^= 0x11D;
    }
    EXP[255] = EXP[0];
  })();

  function gfMul(a, b) {
    if (a === 0 || b === 0) return 0;
    return EXP[(LOG[a] + LOG[b]) % 255];
  }

  function polyMul(a, b) {
    var out = new Array(a.length + b.length - 1).fill(0);
    for (var i = 0; i < a.length; i++) {
      for (var j = 0; j < b.length; j++) {
        out[i + j] ^= gfMul(a[i], b[j]);
      }
    }
    return out;
  }

  var GEN_CACHE = {};
  function rsGenerator(degree) {
    if (GEN_CACHE[degree]) return GEN_CACHE[degree];
    var gen = [1];
    for (var i = 0; i < degree; i++) gen = polyMul(gen, [1, EXP[i]]);
    GEN_CACHE[degree] = gen;
    return gen;
  }

  function rsRemainder(data, degree) {
    var gen = rsGenerator(degree);
    var rem = new Array(degree).fill(0);
    for (var i = 0; i < data.length; i++) {
      var factor = data[i] ^ rem[0];
      rem.shift();
      rem.push(0);
      for (var j = 0; j < degree; j++) rem[j] ^= gfMul(gen[j + 1], factor);
    }
    return rem;
  }

  // --- version tables (level L, uniform blocks) ---------------------------
  var VERSIONS = {
    1: { dataTotal: 19, ecc: 7, blocks: 1 },
    2: { dataTotal: 34, ecc: 10, blocks: 1 },
    3: { dataTotal: 55, ecc: 15, blocks: 1 },
    4: { dataTotal: 80, ecc: 20, blocks: 1 },
    5: { dataTotal: 108, ecc: 26, blocks: 1 },
    6: { dataTotal: 136, ecc: 18, blocks: 2 },
    7: { dataTotal: 156, ecc: 20, blocks: 2 },
    8: { dataTotal: 194, ecc: 24, blocks: 2 },
    9: { dataTotal: 232, ecc: 30, blocks: 2 },
  };

  var ALIGN = {
    1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30],
    6: [6, 34], 7: [6, 22, 38], 8: [6, 24, 42], 9: [6, 26, 46],
  };

  function utf8Bytes(str) {
    if (typeof TextEncoder !== "undefined") {
      return Array.prototype.slice.call(new TextEncoder().encode(str));
    }
    var out = [];
    for (var i = 0; i < str.length; i++) {
      var c = str.charCodeAt(i);
      if (c < 0x80) out.push(c);
      else if (c < 0x800) out.push(0xC0 | (c >> 6), 0x80 | (c & 63));
      else if (c < 0xD800 || c >= 0xE000)
        out.push(0xE0 | (c >> 12), 0x80 | ((c >> 6) & 63), 0x80 | (c & 63));
      else {
        i++;
        var cp = 0x10000 + (((c & 0x3FF) << 10) | (str.charCodeAt(i) & 0x3FF));
        out.push(0xF0 | (cp >> 18), 0x80 | ((cp >> 12) & 63),
                 0x80 | ((cp >> 6) & 63), 0x80 | (cp & 63));
      }
    }
    return out;
  }

  // --- data encoding -------------------------------------------------------
  function encodeCodewords(text) {
    var bytes = utf8Bytes(text);
    var version = 0, meta = null;
    for (var v = 1; v <= 9; v++) {
      var m = VERSIONS[v];
      if (4 + 8 + bytes.length * 8 <= m.dataTotal * 8) {
        version = v;
        meta = m;
        break;
      }
    }
    if (!version) throw new Error("QRCode: text too long for supported versions");

    var bits = [];
    function append(val, len) {
      for (var i = len - 1; i >= 0; i--) bits.push((val >>> i) & 1);
    }
    append(0x4, 4); // byte mode
    append(bytes.length, 8);
    for (var i = 0; i < bytes.length; i++) append(bytes[i], 8);

    var capacityBits = meta.dataTotal * 8;
    var terminator = Math.min(4, capacityBits - bits.length);
    for (var t = 0; t < terminator; t++) bits.push(0);
    while (bits.length % 8 !== 0) bits.push(0);

    var codewords = [];
    for (var b = 0; b < bits.length; b += 8) {
      var byteVal = 0;
      for (var k = 0; k < 8; k++) byteVal = (byteVal << 1) | bits[b + k];
      codewords.push(byteVal);
    }
    var pad = [0xEC, 0x11], pi = 0;
    while (codewords.length < meta.dataTotal) {
      codewords.push(pad[pi++ % 2]);
    }

    var perBlock = meta.dataTotal / meta.blocks;
    var dataBlocks = [], eccBlocks = [];
    for (var blk = 0; blk < meta.blocks; blk++) {
      var chunk = codewords.slice(blk * perBlock, (blk + 1) * perBlock);
      dataBlocks.push(chunk);
      eccBlocks.push(rsRemainder(chunk, meta.ecc));
    }

    var interleaved = [];
    for (var c = 0; c < perBlock; c++)
      for (var bi = 0; bi < meta.blocks; bi++) interleaved.push(dataBlocks[bi][c]);
    for (var e = 0; e < meta.ecc; e++)
      for (var bj = 0; bj < meta.blocks; bj++) interleaved.push(eccBlocks[bj][e]);

    var dataBits = [];
    for (var ci = 0; ci < interleaved.length; ci++) {
      for (var bit = 7; bit >= 0; bit--) dataBits.push((interleaved[ci] >>> bit) & 1);
    }
    return { version: version, bits: dataBits };
  }

  // --- matrix construction -------------------------------------------------
  function buildMatrix(version, dataBits) {
    var size = version * 4 + 17;
    var modules = [], isFunc = [];
    for (var r = 0; r < size; r++) {
      modules.push(new Array(size).fill(false));
      isFunc.push(new Array(size).fill(false));
    }
    function setFunc(row, col, dark) {
      modules[row][col] = !!dark;
      isFunc[row][col] = true;
    }

    // finder patterns + separators
    function finder(row, col) {
      for (var dr = -1; dr <= 7; dr++) {
        for (var dc = -1; dc <= 7; dc++) {
          var r = row + dr, c = col + dc;
          if (r < 0 || r >= size || c < 0 || c >= size) continue;
          var dark = dr >= 0 && dr <= 6 && dc >= 0 && dc <= 6 &&
            (dr === 0 || dr === 6 || dc === 0 || dc === 6 ||
             (dr >= 2 && dr <= 4 && dc >= 2 && dc <= 4));
          setFunc(r, c, dark);
        }
      }
    }
    finder(0, 0);
    finder(0, size - 7);
    finder(size - 7, 0);

    // timing patterns
    for (var i = 8; i < size - 8; i++) {
      setFunc(6, i, i % 2 === 0);
      setFunc(i, 6, i % 2 === 0);
    }

    // alignment patterns
    var pos = ALIGN[version];
    for (var a = 0; a < pos.length; a++) {
      for (var b = 0; b < pos.length; b++) {
        var ar = pos[a], ac = pos[b];
        if ((ar === pos[0] && ac === pos[0]) ||
            (ar === pos[0] && ac === pos[pos.length - 1]) ||
            (ar === pos[pos.length - 1] && ac === pos[0])) continue;
        for (var dr2 = -2; dr2 <= 2; dr2++) {
          for (var dc2 = -2; dc2 <= 2; dc2++) {
            var dark2 = Math.max(Math.abs(dr2), Math.abs(dc2)) !== 1;
            setFunc(ar + dr2, ac + dc2, dark2);
          }
        }
      }
    }

    // reserve format info areas (values written later per mask)
    function reserveFormat() {
      for (var i = 0; i <= 5; i++) setFunc(i, 8, false);
      setFunc(7, 8, false);
      setFunc(8, 8, false);
      setFunc(8, 7, false);
      for (var j = 9; j < 15; j++) setFunc(8, 14 - j, false);
      for (var k = 0; k < 8; k++) setFunc(8, size - 1 - k, false);
      for (var m = 8; m < 15; m++) setFunc(size - 15 + m, 8, false);
      setFunc(size - 8, 8, true); // always-dark module
    }
    reserveFormat();

    // version info for v >= 7
    if (version >= 7) {
      var vbits = versionBits(version);
      for (var vi = 0; vi < 18; vi++) {
        var bit = (vbits >>> vi) & 1;
        var x = size - 11 + (vi % 3);
        var y = Math.floor(vi / 3);
        setFunc(y, x, bit);
        setFunc(x, y, bit);
      }
    }

    // data placement (zig-zag)
    var idx = 0;
    for (var right = size - 1; right >= 1; right -= 2) {
      if (right === 6) right = 5;
      for (var vert = 0; vert < size; vert++) {
        for (var j2 = 0; j2 < 2; j2++) {
          var col = right - j2;
          var upward = ((right + 1) & 2) === 0;
          var row = upward ? size - 1 - vert : vert;
          if (!isFunc[row][col] && idx < dataBits.length) {
            modules[row][col] = dataBits[idx] === 1;
            idx++;
          }
        }
      }
    }

    return { size: size, modules: modules, isFunc: isFunc };
  }

  function versionBits(version) {
    var rem = version;
    for (var i = 0; i < 12; i++) rem = (rem << 1) ^ ((rem >>> 11) * 0x1F25);
    return (version << 12) | rem;
  }

  function formatBits(mask) {
    var data = (1 << 3) | mask; // ECC level L == 1
    var rem = data;
    for (var i = 0; i < 10; i++) rem = (rem << 1) ^ ((rem >>> 9) * 0x537);
    return ((data << 10) | rem) ^ 0x5412;
  }

  function drawFormat(m, size, mask) {
    var bits = formatBits(mask);
    function bit(i) { return ((bits >>> i) & 1) !== 0; }
    for (var i = 0; i <= 5; i++) m[i][8] = bit(i);
    m[7][8] = bit(6);
    m[8][8] = bit(7);
    m[8][7] = bit(8);
    for (var j = 9; j < 15; j++) m[8][14 - j] = bit(j);
    for (var k = 0; k < 8; k++) m[8][size - 1 - k] = bit(k);
    for (var l = 8; l < 15; l++) m[size - 15 + l][8] = bit(l);
    m[size - 8][8] = true;
  }

  function maskFn(mask, row, col) {
    switch (mask) {
      case 0: return (row + col) % 2 === 0;
      case 1: return row % 2 === 0;
      case 2: return col % 3 === 0;
      case 3: return (row + col) % 3 === 0;
      case 4: return (Math.floor(row / 2) + Math.floor(col / 3)) % 2 === 0;
      case 5: return ((row * col) % 2) + ((row * col) % 3) === 0;
      case 6: return (((row * col) % 2) + ((row * col) % 3)) % 2 === 0;
      case 7: return (((row + col) % 2) + ((row * col) % 3)) % 2 === 0;
    }
    return false;
  }

  function applyMask(m, isFunc, size, mask) {
    for (var r = 0; r < size; r++) {
      for (var c = 0; c < size; c++) {
        if (!isFunc[r][c] && maskFn(mask, r, c)) m[r][c] = !m[r][c];
      }
    }
  }

  function penalty(m) {
    var size = m.length, score = 0, i, j, run, color;

    for (i = 0; i < size; i++) {
      run = 1;
      for (j = 1; j < size; j++) {
        if (m[i][j] === m[i][j - 1]) run++;
        else { if (run >= 5) score += 3 + (run - 5); run = 1; }
      }
      if (run >= 5) score += 3 + (run - 5);
    }
    for (j = 0; j < size; j++) {
      run = 1;
      for (i = 1; i < size; i++) {
        if (m[i][j] === m[i - 1][j]) run++;
        else { if (run >= 5) score += 3 + (run - 5); run = 1; }
      }
      if (run >= 5) score += 3 + (run - 5);
    }
    for (i = 0; i < size - 1; i++) {
      for (j = 0; j < size - 1; j++) {
        if (m[i][j] === m[i][j + 1] && m[i][j] === m[i + 1][j] &&
            m[i][j] === m[i + 1][j + 1]) score += 3;
      }
    }
    var p1 = [false, false, false, false, true, false, true, true, true, false, true];
    var p2 = [true, false, true, true, true, false, true, false, false, false, false];
    function matches(arr, start, pat) {
      for (var k = 0; k < 11; k++) if (arr[start + k] !== pat[k]) return false;
      return true;
    }
    for (i = 0; i < size; i++) {
      for (j = 0; j <= size - 11; j++) {
        if (matches(m[i], j, p1) || matches(m[i], j, p2)) score += 40;
      }
    }
    for (j = 0; j < size; j++) {
      var col = [];
      for (i = 0; i < size; i++) col.push(m[i][j]);
      for (i = 0; i <= size - 11; i++) {
        if (matches(col, i, p1) || matches(col, i, p2)) score += 40;
      }
    }
    var dark = 0;
    for (i = 0; i < size; i++)
      for (j = 0; j < size; j++) if (m[i][j]) dark++;
    var ratio = (dark * 100) / (size * size);
    score += Math.floor(Math.abs(ratio - 50) / 5) * 10;
    return score;
  }

  function cloneMatrix(m) {
    var out = [];
    for (var r = 0; r < m.length; r++) out.push(m[r].slice());
    return out;
  }

  function encode(text) {
    var data = encodeCodewords(text);
    var built = buildMatrix(data.version, data.bits);
    var size = built.size, isFunc = built.isFunc;
    var best = 0, bestScore = Infinity, bestMatrix = null;
    for (var mask = 0; mask < 8; mask++) {
      var candidate = cloneMatrix(built.modules);
      applyMask(candidate, isFunc, size, mask);
      drawFormat(candidate, size, mask);
      var score = penalty(candidate);
      if (score < bestScore) {
        bestScore = score;
        best = mask;
        bestMatrix = candidate;
      }
    }
    return { size: size, modules: bestMatrix, mask: best };
  }

  function toSvg(text, opts) {
    opts = opts || {};
    var border = opts.border == null ? 4 : opts.border;
    var dark = opts.dark || "#000000";
    var light = opts.light || "#ffffff";
    var code = encode(text);
    var size = code.size, dim = size + border * 2;
    var path = "";
    for (var r = 0; r < size; r++) {
      for (var c = 0; c < size; c++) {
        if (code.modules[r][c]) {
          path += "M" + (c + border) + "," + (r + border) + "h1v1h-1z";
        }
      }
    }
    return '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ' + dim + " " +
      dim + '" shape-rendering="crispEdges" role="img">' +
      '<rect width="100%" height="100%" fill="' + light + '"/>' +
      '<path d="' + path + '" fill="' + dark + '"/></svg>';
  }

  function toCanvas(text, canvas, opts) {
    opts = opts || {};
    var border = opts.border == null ? 4 : opts.border;
    var dark = opts.dark || "#000000";
    var light = opts.light || "#ffffff";
    var scale = opts.scale || 4;
    var code = encode(text);
    var dim = (code.size + border * 2) * scale;
    canvas.width = dim;
    canvas.height = dim;
    var ctx = canvas.getContext("2d");
    ctx.fillStyle = light;
    ctx.fillRect(0, 0, dim, dim);
    ctx.fillStyle = dark;
    for (var r = 0; r < code.size; r++) {
      for (var c = 0; c < code.size; c++) {
        if (code.modules[r][c]) {
          ctx.fillRect((c + border) * scale, (r + border) * scale, scale, scale);
        }
      }
    }
  }

  global.QRCode = { encode: encode, toSvg: toSvg, toCanvas: toCanvas };
})(typeof window !== "undefined" ? window : this);