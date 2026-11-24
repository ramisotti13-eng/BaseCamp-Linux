// The light ring of the Makalu picture, live. A lighting state {effect, speed, direction, brightness, color1, color2, leds}
// becomes a plan (pure, tested) and the plan becomes a few stacked layers inside a clipped box. Everything moves by CSS
// keyframes on transform/opacity of whole layers (compositor only, no script per frame, no timers); the keyframes are in
// css/mouse-light.css. Static and Off have no animation at all. Animations are paused while the window has no focus
// and end with the element leaving the page; with reduced motion the CSS stops them.

export const NUM_LEDS = 8;
const SPEED = { slow: 2, medium: 1, fast: 0.5 };           // seconds per turn of the rainbow ("medium" = Base Camp's 1 s)
const PACE = { slow: 1.6, medium: 1, fast: 0.6 };          // factor on the slower effects' periods
export const cycleSeconds = (speed) => SPEED[speed] || 1;
const pace = (speed) => PACE[speed] || 1;

/** Base Camp's rainbow: violet, indigo, blue, green, yellow, orange, red (closed to a ring). */
export const RAINBOW = ["#8b00ff", "#4b0082", "#0000ff", "#008000", "#ffff00", "#ffa500", "#ff0000"];
const RGB_CYCLE = ["#ff0000", "#00ff00", "#0000ff"];
export const RAINBOW_GRADIENT = "conic-gradient(" + [...RAINBOW, RAINBOW[0]].join(",") + ")";

const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
export const hexOf = (c) => "#" + (c || [0, 0, 0]).map((v) => clamp(v | 0, 0, 255).toString(16).padStart(2, "0")).join("");
const isBlack = (c) => !c || (c[0] | c[1] | c[2]) === 0;
/** Base Camp shows the light at brightness(1.2); the setting scales that. */
export const brightnessFactor = (b) => (clamp(Number.isFinite(+b) ? +b : 100, 0, 100) / 100) * 1.2;

/** The mouse's eight LEDs in the order of the core: 0 top left down to 3 bottom left, then 4 bottom right up to 7 top right.
 *  Custom draws the left four as bands of the ring's left half, top to bottom, and the right four on its right half. */
export const ledBands = (leds) => {
  const c = (i) => hexOf(leds && leds[i]);
  const band = (a) => "linear-gradient(to bottom," + a.map((x, k) => c(x) + " " + k * 25 + "% " + (k + 1) * 25 + "%").join(",") + ")";
  return [band([0, 1, 2, 3]), band([7, 6, 5, 4])];
};

/** What to draw: {kind, bright, moving, layers:[{bg, anim, dur, delay, spin, wedge, from}]}. anim is a keyframes name
 *  in the css. `spin` layers turn (rainbow); `wedge` (degrees, from `from`) clips a solid colour to a slice of the ring. */
export function plan(s) {
  s = s || {};
  const eff = s.effect || "static";
  const sp = s.speed || "medium";
  const c1 = hexOf(s.color1 || [0, 118, 204]);
  const c2 = isBlack(s.color2) ? null : hexOf(s.color2);
  const bright = brightnessFactor(s.brightness);
  const out = (kind, layers) => ({ kind, bright, moving: layers.some((l) => !!l.anim), layers });
  if (eff === "off" || bright === 0) return { kind: "off", bright: 0, moving: false, layers: [] };
  if (eff === "rainbow") {
    const rev = s.direction === "left";
    return out("rainbow", [{ bg: RAINBOW_GRADIENT, anim: rev ? "ml-spin-rev" : "ml-spin", dur: cycleSeconds(sp), spin: true }]);
  }
  if (eff === "custom") { const [l, r] = ledBands(s.leds); return out("custom", [{ bg: l, half: "l" }, { bg: r, half: "r" }]); }
  if (eff === "breathing" || eff === "rgb_breathing") {
    const cols = eff === "rgb_breathing" ? RGB_CYCLE : c2 && c2 !== c1 ? [c1, c2] : [c1];
    const p = 3 * pace(sp);                                   // seconds for one fade in and out
    const n = cols.length;
    return out(eff, cols.map((c, i) => ({ bg: c, anim: "ml-breath-" + n, dur: p * n, delay: -(n - i) * p })));
  }
  // Base Camp's ring stays dark until a click; flash() lights it briefly.
  if (eff === "responsive") return out("responsive", [{ bg: c1, hit: true }]);
  if (eff === "yeti") {                                       // colour 2 all round, colour 1 flickering in slices
    const wedges = [0, 1, 2].map((i) => ({ bg: c1, anim: "ml-yeti", dur: [1.7, 2.3, 3.1][i] * pace(sp), delay: -i * 0.9, wedge: 100, from: i * 120 }));
    return out("yeti", c2 ? [{ bg: c2 }, ...wedges] : wedges);
  }
  return out("static", [{ bg: c1 }]);
}

// ── where the ring sits on the picture ────────────────────────────────────

/** The ring's box [x, y, w, h] (0..1 of the picture) from its measured pixel extent (x0, y0 .. x1, y1 inclusive). */
export const ringBox = (W, H, x0, y0, x1, y1) => [x0 / W, y0 / H, (x1 - x0 + 1) / W, (y1 - y0 + 1) / H];

/** CSS left/top/width/height (percent of the picture box) of a normalized rect. */
export function rectCss(r) {
  const p = (v) => +(v * 100).toFixed(3) + "%";
  return `left:${p(r[0])};top:${p(r[1])};width:${p(r[2])};height:${p(r[3])}`;
}

/** Width and height of the square a rainbow turns in so that it still covers the ring's box at every angle,
 *  as percent of that box: [w%, h%]. aspect = box width / box height in the same unit. */
export function spinSize(aspect) {
  const d = Math.hypot(aspect, 1);               // diagonal, in units of the box's height
  return [+(d / aspect * 100).toFixed(2), +(d * 100).toFixed(2)];
}

// ── the live layer ────────────────────────────────────────────────────────

const live = new Set();
const focused = () => typeof document === "undefined" || ((typeof document.hasFocus !== "function" || document.hasFocus()) && !document.hidden);
function syncAll() {
  const on = focused();
  for (const l of live) {
    if (!l.el.isConnected) { live.delete(l); continue; }
    l.el.classList.toggle("ml-paused", !on);
  }
}
let listening = false;
function listen() {
  if (listening || typeof window === "undefined") return;
  listening = true;
  window.addEventListener("focus", syncAll);
  window.addEventListener("blur", syncAll);
  document.addEventListener("visibilitychange", syncAll);
}

/** The light layer of one picture. `ring` = {rect, mode: "behind"|"over", stroke, aspect} (see hotspots.js ringRect);
 *  returns {el, set(state, {still}), dispose()}. Place `el` inside the picture box: "behind" before the photo,
 *  "over" after it. set(null) hides the light. */
export function createLight(ring) {
  listen();
  const el = document.createElement("div");
  el.className = "mlight ml-" + ring.mode;
  el.setAttribute("aria-hidden", "true");
  el.style.cssText = rectCss(ring.rect) + (ring.mode === "over" ? `;--ml-s:${+(ring.stroke * 100).toFixed(3)}%` : "");
  el.hidden = true;
  const self = { el, set, flash, dispose() { live.delete(self); el.remove(); } };
  let key = null;
  /** A short flash of the "responsive" light (the ring is dark until the picture is clicked). */
  function flash() {
    for (const d of el.children) {
      if (!d.classList || !d.classList.contains("ml-hit")) continue;
      d.style.animation = "none";
      void d.offsetWidth;
      d.style.animation = "ml-hit .7s ease-out 1";
    }
  }
  function set(state, opts) {
    if (!state) { key = null; el.hidden = true; el.textContent = ""; live.delete(self); return; }
    const p = plan(state);
    const still = !!(opts && opts.still);
    const k = JSON.stringify([p, still]);
    if (k === key) return;
    key = k;
    el.textContent = "";
    el.hidden = p.kind === "off";
    el.style.setProperty("--ml-b", String(p.bright));
    const [sw, sh] = spinSize(ring.aspect || 0.3);
    // Still: one frame; the faders show their first colour, the yeti slices are drawn faintly (css .ml-still).
    const layers = still && /breathing$/.test(p.kind) ? p.layers.slice(0, 1) : p.layers;
    el.classList.toggle("ml-still", still);
    for (const l of layers) {
      const d = document.createElement("div");
      d.className = "ml-layer" + (l.spin ? " ml-turn" : "") + (l.wedge ? " ml-wedge" : "") + (l.hit ? " ml-hit" : "") + (l.half ? " ml-half-" + l.half : "");
      let css = l.wedge ? `background:conic-gradient(from ${l.from}deg,${l.bg} 0,${l.bg} ${l.wedge}deg,transparent ${l.wedge}deg);` : `background:${l.bg};`;
      if (l.spin) css += `width:${sw}%;height:${sh}%;`;
      if (l.anim && !still) css += `animation:${l.anim} ${+l.dur.toFixed(3)}s linear infinite${l.delay ? " " + l.delay.toFixed(3) + "s" : ""};`;
      d.style.cssText = css;
      el.append(d);
    }
    if (p.moving && !still) { live.add(self); el.classList.toggle("ml-paused", !focused()); } else live.delete(self);
  }
  return self;
}
