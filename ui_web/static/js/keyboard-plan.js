// Where everything of a flat Everest keyboard picture goes (keyboard-flat.js draws it), in the layout's
// canvas units: plates, wells (the Everest Max's key recesses), keys, ring LEDs, D-keys, dock. Pure, so
// a test can run it without a page.

export const RING = 200;

const PAD = 8;           // plate edge around the keys (Everest Max)
const GAP = 3;           // between plate and ring
const WELL = 3;          // the recess the Everest Max's keys sit in, around them
// Individuell, measured on Base Camp's picture (main board 655 px wide): the numpad 43 px apart, the
// ring 7 px off the sides and the bottom, 13 px above the top, its squares about 30 px in from each
// corner. In canvas units, the gap is a share of the main board's width.
const SPLIT_GAP = 0.066;
const SPLIT_RING = { top: 10, side: 6, inset: 24 };

export function bbox(rects) {
  const x1 = Math.min(...rects.map((r) => r.x)), y1 = Math.min(...rects.map((r) => r.y));
  const x2 = Math.max(...rects.map((r) => r.x + r.w)), y2 = Math.max(...rects.map((r) => r.y + r.h));
  return { x: x1, y: y1, w: x2 - x1, h: y2 - y1 };
}
export const grow = (b, p) => ({ x: b.x - p, y: b.y - p, w: b.w + 2 * p, h: b.h + 2 * p });

/** opts: {split: the numpad a piece of its own with the ring around each piece (Individuell),
 *  ring: draw the side ring's LEDs (only the RGB screens do), joined: the numpad against the main
 *  board (key binding, as Base Camp draws it there)}. Keys carry `kid`, their number for a binding. */
export function plan(model, L, modules, opts = {}) {
  const { split = false, ring: withRing = false, joined = false } = opts;
  const plates = [], wells = [], keys = [], leds = [], dkeys = [];
  let dock = null;
  if (model === "everest60") {
    const ks = L.keys.map((k) => ({ id: k.led, label: k.label, x: k.x, y: k.y, w: k.w, h: k.h, mod: "core", lit: k.led != null }));
    keys.push(...ks);
    plates.push({ ...grow(bbox(ks), 6), mod: "core" });
    const s = L.side_led_size || 10;
    if (withRing) L.side.forEach((p, i) => leds.push({ id: RING + i, x: p.x - s / 2, y: p.y - s / 2, s }));
    return { plates, wells, keys, leds, dkeys, dock, ring: s };
  }
  const padX = L.numpad_x;
  const core = L.keys.filter((k) => k.x < padX), pad = L.keys.filter((k) => k.x >= padX);
  const mk = (k, mod) => ({ id: k.led, kid: k.kid ?? null, label: k.label, x: k.x, y: k.y, w: k.w, h: k.h, mod, lit: k.led != null });
  const coreKeys = core.map((k) => mk(k, "core"));
  keys.push(...coreKeys);
  const coreBox = grow(bbox(coreKeys), PAD);
  // The case reaches up to the numpad's top (the logo strip above the F-row), as in the original;
  // the media dock lies on that strip.
  if (L.board_top != null && L.board_top < coreBox.y) { coreBox.h += coreBox.y - L.board_top; coreBox.y = L.board_top; }
  plates.push({ ...coreBox, mod: "core" });
  wells.push(grow(bbox(coreKeys), WELL));
  const S = L.side_led_size || 9, z = L.side_zones;
  // Along an edge: corner to corner, or (split) at even steps that stay clear of the corners.
  const m = split ? SPLIT_RING.inset : 0;
  const along = (n, i, from, len) => from + m + (n > 1 ? i * (len - 2 * m - S) / (n - 1) : (len - 2 * m - S) / 2);
  const spreadH = (ids, b, y) => ids.forEach((id, i) => leds.push({ id: RING + id, s: S, y, x: along(ids.length, i, b.x, b.w) }));
  const spreadV = (ids, b, x) => ids.forEach((id, i) => leds.push({ id: RING + id, s: S, x, y: along(ids.length, i, b.y, b.h) }));
  const top = split ? SPLIT_RING.top : GAP, side = split ? SPLIT_RING.side : GAP;
  const ring = (b, zs) => {
    if (!withRing) return;
    spreadH(zs[0], b, b.y - top - S); spreadV(zs[1], b, b.x + b.w + side);
    spreadH(zs[2], b, b.y + b.h + side); spreadV(zs[3], b, b.x - side - S);
  };
  ring(coreBox, [z[0], z[1], z[2], z[3]]);
  if (modules.numpad) {
    const nk = pad.map((k) => mk(k, "numpad"));
    const dk = L.dkeys.map((d) => ({ idx: d.idx, kid: d.kid ?? null, label: d.label, x: d.x, y: d.y, w: d.w, h: d.h }));
    const nb = grow(bbox([...nk, ...dk]), PAD);
    const dx = split ? coreBox.x + coreBox.w * (1 + SPLIT_GAP) - nb.x : joined ? coreBox.x + coreBox.w - nb.x : 0;
    if (dx) for (const r of [...nk, ...dk, nb]) r.x += dx;
    keys.push(...nk);
    dkeys.push(...dk);
    plates.push({ ...nb, mod: "numpad" });
    wells.push(grow(bbox(nk), WELL));
    ring(nb, [z[4], z[5], z[6], z[7]]);
  }
  if (modules.dock) {
    const d = L.dock;
    dock = { ...d, dial: L.dial };
    plates.push({ x: d.x, y: d.y, w: d.w, h: d.h, mod: "dock" });
  }
  return { plates, wells, keys, leds, dkeys, dock, ring: S };
}
