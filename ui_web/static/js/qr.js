// A small QR code encoder for the pairing URL (Settings > Remote access): byte mode, error
// correction level M, versions 1 to 10 (up to 213 bytes of UTF-8). The mask is the one of the
// eight with the lowest penalty, the rules read as libqrencode reads them (its matrices are the
// reference this file is tested against, module for module).
//
//   qrMatrix(text)          -> boolean[][] (true = dark), size 21 to 57, no quiet zone
//   qrCanvas(matrix, px)    -> a <canvas> with the code and a quiet zone of 4 modules

// Per version 1..10 at level M: [EC codewords per block, [[blocks, data codewords per block], ...]].
const BLOCKS = [
  null,
  [10, [[1, 16]]],
  [16, [[1, 28]]],
  [26, [[1, 44]]],
  [18, [[2, 32]]],
  [24, [[2, 43]]],
  [16, [[4, 27]]],
  [18, [[4, 31]]],
  [22, [[2, 38], [2, 39]]],
  [22, [[3, 36], [2, 37]]],
  [26, [[4, 43], [1, 44]]],
];
const ALIGN = [null, [], [6, 18], [6, 22], [6, 26], [6, 30], [6, 34], [6, 22, 38], [6, 24, 42], [6, 26, 46], [6, 28, 50]];
const REMAINDER_BITS = [0, 0, 7, 7, 7, 7, 7, 0, 0, 0, 0];
const MAX_VERSION = 10;
const EC_M = 0;           // the two format bits of level M

// ── Reed-Solomon over GF(256), polynomial 0x11D ─────────────────────────────

const EXP = new Uint8Array(512);
const LOG = new Uint8Array(256);
(() => {
  let x = 1;
  for (let i = 0; i < 255; i++) {
    EXP[i] = x; LOG[x] = i;
    x <<= 1;
    if (x & 0x100) x ^= 0x11d;
  }
  for (let i = 255; i < 512; i++) EXP[i] = EXP[i - 255];
})();

const mul = (a, b) => (a && b ? EXP[LOG[a] + LOG[b]] : 0);

function generator(degree) {
  // (x - a^0)(x - a^1)...(x - a^(degree-1)), highest coefficient first, the leading 1 left out.
  let g = [1];
  for (let i = 0; i < degree; i++) {
    const next = new Array(g.length + 1).fill(0);
    for (let j = 0; j < g.length; j++) {
      next[j] ^= g[j];
      next[j + 1] ^= mul(g[j], EXP[i]);
    }
    g = next;
  }
  return g.slice(1);
}

function ecCodewords(data, degree) {
  const gen = generator(degree);
  const rem = new Array(degree).fill(0);
  for (const byte of data) {
    const f = byte ^ rem.shift();
    rem.push(0);
    for (let i = 0; i < degree; i++) rem[i] ^= mul(gen[i], f);
  }
  return rem;
}

// ── data codewords ──────────────────────────────────────────────────────────

const dataCapacity = (v) => BLOCKS[v][1].reduce((n, [count, len]) => n + count * len, 0);
const countBits = (v) => (v < 10 ? 8 : 16);

function utf8(text) {
  return Array.from(new TextEncoder().encode(String(text)));
}

function pickVersion(bytes) {
  for (let v = 1; v <= MAX_VERSION; v++) {
    if (4 + countBits(v) + 8 * bytes.length <= 8 * dataCapacity(v)) return v;
  }
  throw new Error("too long for a QR code of version " + MAX_VERSION + ": " + bytes.length + " bytes");
}

function dataCodewords(bytes, v) {
  const bits = [];
  const put = (val, n) => { for (let i = n - 1; i >= 0; i--) bits.push((val >>> i) & 1); };
  put(0b0100, 4);                       // byte mode
  put(bytes.length, countBits(v));
  for (const b of bytes) put(b, 8);
  const cap = 8 * dataCapacity(v);
  put(0, Math.min(4, cap - bits.length));      // terminator
  while (bits.length % 8) bits.push(0);
  const out = [];
  for (let i = 0; i < bits.length; i += 8) out.push(bits.slice(i, i + 8).reduce((a, b) => (a << 1) | b, 0));
  for (let pad = 0xec; out.length < cap / 8; pad ^= 0xec ^ 0x11) out.push(pad);
  return out;
}

function interleave(data, v) {
  const [ecLen, groups] = BLOCKS[v];
  const blocks = [];
  let at = 0;
  for (const [count, len] of groups) {
    for (let i = 0; i < count; i++) {
      const d = data.slice(at, at + len);
      at += len;
      blocks.push({ d, e: ecCodewords(d, ecLen) });
    }
  }
  const out = [];
  const longest = Math.max(...blocks.map((b) => b.d.length));
  for (let i = 0; i < longest; i++) for (const b of blocks) if (i < b.d.length) out.push(b.d[i]);
  for (let i = 0; i < ecLen; i++) for (const b of blocks) out.push(b.e[i]);
  return out;
}

// ── the matrix ──────────────────────────────────────────────────────────────

function functionPatterns(v) {
  const size = 17 + 4 * v;
  const dark = Array.from({ length: size }, () => new Array(size).fill(false));
  const fixed = Array.from({ length: size }, () => new Array(size).fill(false));
  const set = (r, c, on) => { dark[r][c] = on; fixed[r][c] = true; };
  // Finders with their light separators.
  for (const [r0, c0] of [[3, 3], [3, size - 4], [size - 4, 3]]) {
    for (let dr = -4; dr <= 4; dr++) for (let dc = -4; dc <= 4; dc++) {
      const r = r0 + dr, c = c0 + dc;
      if (r < 0 || c < 0 || r >= size || c >= size) continue;
      const d = Math.max(Math.abs(dr), Math.abs(dc));
      set(r, c, d !== 2 && d !== 4);
    }
  }
  // Timing.
  for (let i = 8; i < size - 8; i++) { set(6, i, i % 2 === 0); set(i, 6, i % 2 === 0); }
  // Alignment patterns, except in the three corners of the finders (those on the timing
  // lines, from version 7 on, are drawn over them).
  const pos = ALIGN[v];
  const last = pos.length - 1;
  pos.forEach((r0, ri) => pos.forEach((c0, ci) => {
    if ((ri === 0 && ci === 0) || (ri === 0 && ci === last) || (ri === last && ci === 0)) return;
    for (let dr = -2; dr <= 2; dr++) for (let dc = -2; dc <= 2; dc++) {
      set(r0 + dr, c0 + dc, Math.max(Math.abs(dr), Math.abs(dc)) !== 1);
    }
  }));
  // Format areas (written per mask), the dark module.
  for (let i = 0; i < 9; i++) { if (!fixed[8][i]) set(8, i, false); if (!fixed[i][8]) set(i, 8, false); }
  for (let i = 0; i < 8; i++) { set(8, size - 1 - i, false); set(size - 1 - i, 8, false); }
  set(size - 8, 8, true);
  // Version information (7 and up).
  if (v >= 7) {
    let rem = v;
    for (let i = 0; i < 12; i++) rem = (rem << 1) ^ ((rem >>> 11) * 0x1f25);
    const bits = (v << 12) | rem;
    for (let i = 0; i < 18; i++) {
      const on = ((bits >>> i) & 1) === 1;
      const a = size - 11 + (i % 3), b = Math.floor(i / 3);
      set(b, a, on);
      set(a, b, on);
    }
  }
  return { size, dark, fixed };
}

function placeData(m, codewords, v) {
  const { size, dark, fixed } = m;
  const total = codewords.length * 8 + REMAINDER_BITS[v];
  let i = 0;
  for (let right = size - 1; right >= 1; right -= 2) {
    if (right === 6) right = 5;
    const upward = ((right + 1) & 2) === 0;
    for (let k = 0; k < size; k++) {
      const r = upward ? size - 1 - k : k;
      for (let j = 0; j < 2; j++) {
        const c = right - j;
        if (fixed[r][c] || i >= total) continue;
        const byte = codewords[i >> 3];
        dark[r][c] = byte !== undefined && ((byte >>> (7 - (i & 7))) & 1) === 1;   // remainder bits: light
        i++;
      }
    }
  }
}

const MASKS = [
  (r, c) => (r + c) % 2 === 0,
  (r) => r % 2 === 0,
  (r, c) => c % 3 === 0,
  (r, c) => (r + c) % 3 === 0,
  (r, c) => (Math.floor(r / 2) + Math.floor(c / 3)) % 2 === 0,
  (r, c) => ((r * c) % 2) + ((r * c) % 3) === 0,
  (r, c) => (((r * c) % 2) + ((r * c) % 3)) % 2 === 0,
  (r, c) => (((r + c) % 2) + ((r * c) % 3)) % 2 === 0,
];

function formatBits(mask) {
  const data = (EC_M << 3) | mask;
  let rem = data;
  for (let i = 0; i < 10; i++) rem = (rem << 1) ^ ((rem >>> 9) * 0x537);
  return ((data << 10) | rem) ^ 0x5412;
}

function withMask(m, mask) {
  const { size, dark, fixed } = m;
  const out = dark.map((row, r) => row.map((on, c) => (fixed[r][c] ? on : on !== MASKS[mask](r, c))));
  const bits = formatBits(mask);
  const bit = (i) => ((bits >>> i) & 1) === 1;
  for (let i = 0; i <= 5; i++) out[i][8] = bit(i);
  out[7][8] = bit(6);
  out[8][8] = bit(7);
  out[8][7] = bit(8);
  for (let i = 9; i < 15; i++) out[8][14 - i] = bit(i);
  for (let i = 0; i < 8; i++) out[8][size - 1 - i] = bit(i);
  for (let i = 8; i < 15; i++) out[size - 15 + i][8] = bit(i);
  out[size - 8][8] = true;
  return out;
}

// ── the penalty (libqrencode's reading of the four rules) ────────────────────

// Runs of one line, the first counting light: a line that starts dark gets a light run of
// "-1" in front, so the odd indexes are always the dark runs.
function runs(line) {
  const out = line[0] ? [-1, 1] : [1];
  for (let i = 1; i < line.length; i++) {
    if (line[i] !== line[i - 1]) out.push(1);
    else out[out.length - 1]++;
  }
  return out;
}

function lineScore(line) {
  const run = runs(line);
  const n = run.length;
  let score = 0;
  for (let i = 0; i < n; i++) {
    if (run[i] >= 5) score += 3 + (run[i] - 5);                  // rule 1
    // Rule 3: dark-light-dark-light-dark in 1:1:3:1:1 (or a multiple), light on one side
    // four times as long or the edge of the symbol.
    if (i & 1 && i >= 3 && i < n - 2 && run[i] % 3 === 0) {
      const f = run[i] / 3;
      if (run[i - 2] === f && run[i - 1] === f && run[i + 1] === f && run[i + 2] === f) {
        if (i === 3 || run[i - 3] >= 4 * f) score += 40;
        else if (i + 4 >= n || run[i + 3] >= 4 * f) score += 40;
      }
    }
  }
  return score;
}

function penalty(mat) {
  const size = mat.length;
  let score = 0;
  let darkCount = 0;
  for (let r = 0; r < size; r++) {
    for (let c = 0; c < size; c++) {
      if (mat[r][c]) darkCount++;
      if (r && c) {                                                   // rule 2
        const a = mat[r][c];
        if (a === mat[r][c - 1] && a === mat[r - 1][c] && a === mat[r - 1][c - 1]) score += 3;
      }
    }
  }
  for (let r = 0; r < size; r++) score += lineScore(mat[r]);
  for (let c = 0; c < size; c++) score += lineScore(mat.map((row) => row[c]));
  const all = size * size;                                            // rule 4
  const ratio = Math.floor(Math.floor((200 * darkCount + all) / all) / 2);
  score += Math.floor(Math.abs(ratio - 50) / 5) * 10;
  return score;
}

/** The QR code of `text` (UTF-8, byte mode, level M) as rows of booleans, true for dark. */
export function qrMatrix(text) {
  const bytes = utf8(text);
  const v = pickVersion(bytes);
  const m = functionPatterns(v);
  placeData(m, interleave(dataCodewords(bytes, v), v), v);
  let best = null, bestScore = Infinity;
  for (let mask = 0; mask < 8; mask++) {
    const mat = withMask(m, mask);
    const s = penalty(mat);
    if (s < bestScore) { best = mat; bestScore = s; }
  }
  return best;
}

/** A canvas with `matrix` drawn black on white, `px` pixels per module, 4 modules of quiet zone. */
export function qrCanvas(matrix, px) {
  const quiet = 4;
  const n = matrix.length + 2 * quiet;
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = n * px;
  const g = canvas.getContext("2d");
  g.fillStyle = "#fff";
  g.fillRect(0, 0, n * px, n * px);
  g.fillStyle = "#000";
  matrix.forEach((row, r) => row.forEach((on, c) => {
    if (on) g.fillRect((c + quiet) * px, (r + quiet) * px, px, px);
  }));
  return canvas;
}
