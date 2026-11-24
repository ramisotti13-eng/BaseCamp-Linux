// Live effect preview: what a keyboard's firmware effect looks like at time t.
//
// Pure part:   effectFrame(spec, points, t) -> [[r,g,b], ...]   (one colour per point)
//              keyFrame(specs, points, t): a spec per point (the Everest Max's effects per key)
// Runner part: createRunner({points, paint, isConnected, reduced}) with start/update/stop (requestAnimationFrame);
//              a spec {keys: [spec per point]} runs keyFrame.
//
// spec:   {effect, kind: "single"|"dual"|"rainbow", c1, c2, speed 0-100, direction, brightness 0-100}
//         effect is one of static, wave, tornado, breathing, reactive, matrix, yeti, off; the
//         Everest Max's "wave-rainbow" style ids are accepted too (kind is then taken from the id).
//         direction is whatever the screens hold: "→ L→R", "↻ CW" (Everest Max) or "l2r", "ccw" (Everest 60).
// points: [{x, y, aspect}] with x and y in 0..1 over the whole picture (keys, ring and side LEDs
//         alike), so ring LEDs take part by their position. A point's index is also its random seed.

const TAU = Math.PI * 2;
const fract = (v) => v - Math.floor(v);

/** A cheap, deterministic hash of two integers into [0, 1). */
export function hash2(a, b) {
  let n = (Math.imul(a | 0, 374761393) + Math.imul(b | 0, 668265263)) | 0;
  n = Math.imul(n ^ (n >>> 13), 1274126177);
  return (((n ^ (n >>> 16)) >>> 0) % 100000) / 100000;
}

/** Speed 0 (slow) .. 100 (fast) -> seconds for one cycle, 8 s .. 0.8 s. */
export function cycleSeconds(speed) {
  const s = Math.max(0, Math.min(100, speed == null ? 50 : speed));
  return 8 - (s / 100) * 7.2;
}

/** hue in degrees -> [r,g,b] */
export function hsl(hu, s = 1, l = 0.5) {
  const a = s * Math.min(l, 1 - l);
  const f = (n) => { const k = (n + hu / 30) % 12; return Math.round(255 * (l - a * Math.max(-1, Math.min(k - 3, 9 - k, 1)))); };
  return [f(0), f(8), f(4)];
}
const mix = (a, b, f) => a.map((v, i) => Math.round(v + (b[i] - v) * f));
const scale = (c, f) => c.map((v) => Math.round(v * f));

/** Direction text of either device -> "l2r" | "r2l" | "t2b" | "b2t" | "cw" | "ccw". */
export function parseDirection(d) {
  const s = String(d == null ? "" : d);
  if (/ccw/i.test(s)) return "ccw";
  if (/cw/i.test(s)) return "cw";
  if (/t2b|T→B/.test(s)) return "t2b";
  if (/b2t|B→T/.test(s)) return "b2t";
  if (/r2l|R→L/.test(s)) return "r2l";
  return "l2r";
}

/** Splits "breathing-rainbow" into the effect and its colour kind. */
function resolve(spec) {
  let effect = spec.effect || "static", kind = spec.kind || "single";
  const m = /^(.*)-(rainbow|dual)$/.exec(effect);
  if (m) { effect = m[1]; kind = m[2]; }
  if (kind === "solid") kind = "single";
  return { effect, kind };
}

/** Colour of a position on a palette: one colour pulsing, two colours, or the rainbow. */
function palette(kind, c1, c2, pos) {
  if (kind === "rainbow") return hsl(fract(pos) * 360);
  const w = 0.5 - 0.5 * Math.cos(fract(pos) * TAU);
  return kind === "dual" ? mix(c1, c2, w) : scale(c1, 0.12 + 0.88 * w);
}

/** The colour of every point at time t (seconds). `seeds` gives each point its random seed (its index
 *  in a larger picture); without it a point's index is its seed. */
export function effectFrame(spec, points, t, seeds) {
  const { effect, kind } = resolve(spec);
  const c1 = spec.c1 || [255, 0, 0], c2 = spec.c2 || [0, 0, 255];
  const bri = (spec.brightness == null ? 100 : spec.brightness) / 100;
  const cyc = cycleSeconds(spec.speed);
  const ph = t / cyc;                                   // cycles elapsed
  const dir = parseDirection(spec.direction);
  return points.map((p, k) => {
    const i = seeds ? seeds[k] : k;
    let c;
    switch (effect) {
      case "off": c = [0, 0, 0]; break;
      case "static":
        c = kind === "rainbow" ? hsl(p.x * 330) : kind === "dual" ? mix(c1, c2, p.x) : c1; break;
      case "wave": {
        // the band travels towards `dir`: the pattern is a function of (position - time)
        const pos = dir === "r2l" ? 1 - p.x : dir === "t2b" ? p.y : dir === "b2t" ? 1 - p.y : p.x;
        c = palette(kind, c1, c2, pos - ph); break;
      }
      case "tornado": {
        const a = Math.atan2(p.y - 0.5, (p.x - 0.5) * (p.aspect || 1)) / TAU;   // y points down: a growing angle is clockwise
        c = palette(kind, c1, c2, dir === "ccw" ? a + ph : a - ph); break;
      }
      case "breathing": {
        const lvl = 0.5 - 0.5 * Math.cos(fract(ph) * TAU);
        c = kind === "rainbow" ? scale(hsl(fract(ph / 3) * 360), lvl)
          : kind === "dual" ? scale(Math.floor(ph) % 2 ? c2 : c1, lvl) : scale(c1, lvl);
        break;
      }
      case "reactive": {
        // a key press: random keys light up and fade out, each key with its own rhythm
        const per = 1 + hash2(i, 7) * 1.5, x = t / (cyc * per) + hash2(i, 3) * 10;
        const lit = hash2(i, Math.floor(x) + 1000) < 0.14;
        const lvl = lit ? Math.pow(1 - fract(x), 2) : 0;
        const base = kind === "rainbow" ? hsl(p.x * 330) : c1;
        c = mix(scale(base, 0.3), kind === "rainbow" ? [255, 255, 255] : c2, lvl); break;
      }
      case "matrix": {
        // falling streaks, one per column
        const col = Math.floor(p.x * 26), trail = 0.4;
        const head = fract(ph * 0.7 * (0.7 + hash2(col, 5) * 0.6) + hash2(col, 9)) * (1 + trail);
        const d = head - p.y;
        c = d < 0 || d > trail ? [0, 0, 0] : d < trail * 0.12 ? c2 : scale(c1, 1 - d / trail); break;
      }
      case "yeti": {
        // As the keyboard does it (seen on an Everest Max, 2026-11-23): random keys light up and
        // fade out, the others stay dark. Each flash takes one of the two colours (any hue in
        // rainbow mode), each key with its own rhythm.
        const per = 1.2 + hash2(i, 11) * 1.8, x = t / (cyc * per) + hash2(i, 13) * 10, n = Math.floor(x);
        const lit = hash2(i, n + 2000) < 0.18;
        const col = kind === "rainbow" ? hsl(hash2(i, n + 4000) * 360) : hash2(i, n + 3000) < 0.5 ? c1 : c2;
        c = lit ? scale(col, Math.pow(1 - fract(x), 1.5)) : [0, 0, 0]; break;
      }
      default: c = c1;
    }
    return scale(c, bri);
  });
}

/** Effects per key: `specs` holds a spec per point (null: dark). Points that share a spec object
 *  run as one effect, each point keeping its own index as its seed, so a key flashes the same way
 *  whichever keys share its effect. */
export function keyFrame(specs, points, t) {
  const out = new Array(points.length), groups = new Map();
  specs.forEach((s, i) => {
    if (!s) { out[i] = [0, 0, 0]; return; }
    let g = groups.get(s);
    if (!g) groups.set(s, (g = []));
    g.push(i);
  });
  for (const [s, idx] of groups) {
    const cols = effectFrame(s, idx.map((i) => points[i]), t, idx);
    idx.forEach((i, k) => { out[i] = cols[k]; });
  }
  return out;
}

const isStill = (s) => /^(static|off)(-|$)/.test(s.effect || "");

/** A still moment that shows an effect well (used with prefers-reduced-motion). */
export const STILL_T = 1.3;

// While the page scrolls the preview holds still: repainting some 170 keys and ring LEDs per frame
// next to a scroll made it stutter and jump in the window (Qt WebEngine without GPU rendering).
const SCROLL_PAUSE_MS = 250;
let lastScroll = -Infinity;
if (typeof addEventListener === "function") {
  const mark = () => { lastScroll = performance.now(); };
  addEventListener("scroll", mark, { capture: true, passive: true });
  addEventListener("wheel", mark, { capture: true, passive: true });
}
/** Milliseconds between frames: 15 a second is smooth enough for a preview. */
export const FRAME_MS = 66;
/** The preview only runs while the window has the focus: in the background it would keep a
 *  CPU core busy for nobody. */
const focused = () => typeof document === "undefined" || typeof document.hasFocus !== "function" || document.hasFocus();

/** requestAnimationFrame loop. paint(colours) gets one colour per point.
 *  Stops by itself once the stage has been in the document and left it. */
export function createRunner({ points, paint, isConnected, reduced }) {
  let spec = null, raf = 0, t0 = 0, seen = false, idle = 0, last = 0;
  // Static and Off do not change over time: drawn once, no frames after that.
  const still = () => (typeof reduced === "function" ? reduced() : !!reduced)
    || (!!spec && (spec.keys ? spec.keys.every((s) => !s || isStill(s)) : isStill(spec)));
  const draw = (t) => paint(spec.keys ? keyFrame(spec.keys, points, t) : effectFrame(spec, points, t));
  function tick(now) {
    raf = 0;
    if (!spec) return;
    const on = isConnected();
    if (on) seen = true;
    if ((seen && !on) || (!seen && ++idle > 180)) { spec = null; return; }
    if (now - last >= FRAME_MS && now - lastScroll >= SCROLL_PAUSE_MS && focused()) { last = now; draw((now - t0) / 1000); }
    raf = requestAnimationFrame(tick);
  }
  const api = {
    start(s) {
      spec = s;
      if (raf) { cancelAnimationFrame(raf); raf = 0; }
      if (still()) { draw(STILL_T); return; }
      t0 = performance.now(); idle = 0; last = 0; seen = false;
      draw(0);
      raf = requestAnimationFrame(tick);
    },
    /** New settings without restarting the clock. */
    update(s) {
      const running = !!raf;
      spec = s;
      if (still()) { if (raf) { cancelAnimationFrame(raf); raf = 0; } draw(STILL_T); return; }
      if (!running) api.start(s);
    },
    stop() { spec = null; if (raf) cancelAnimationFrame(raf); raf = 0; },
    get running() { return !!spec && !!raf; },
  };
  return api;
}

export const prefersReducedMotion = () => typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;
