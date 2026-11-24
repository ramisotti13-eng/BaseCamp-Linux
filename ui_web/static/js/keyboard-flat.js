// A flat, top-down picture of an Everest keyboard, built from the core's layout data
// (everest_max.layout / everest60.layout): every key is an element of its own, the Everest Max's
// numpad (with D1-D4) and its media dock (the dial) are modules of their own, and on the RGB screens
// the side ring is a row of small squares around the plate (keyboard-plan.js says where everything
// goes). Our own CSS draws plate and caps: the Everest Max as Base Camp shows it, a brushed silver
// case with dark caps whose face takes the key's colour.
//
//   const kb = renderKeyboard(container, { model: "everest_max", layout, modules: {numpad: true, dock: true},
//                                          mode: "rgb" | "binding" | "preview", split, colors, selection,
//                                          onSelect, onKey, onLed, onDial, onBind });
//   kb.setColors({leds, side}) kb.setSelection(ids) kb.setEffect(spec) kb.setKeyEffects(Map(id -> spec))
//   kb.setDisabled(ids) kb.setDKeys(list) kb.setDialLabel(text) kb.setBindings({states, selected, labels})
//
// `split` draws the numpad as a piece of its own with the ring around each piece, as Base Camp's
// Individuell does. Ids: a key is its LED index, a side-ring LED is RING + its index in the ring.
// "binding" is Base Camp's key binding picture: the numpad against the main board, no ring, every key,
// D-key and dock button a button named by its key number (`kid`, onBind(kid)), Fn lighter and not
// bindable, the dial showing its menu and ↺ ↻ above it for turning it.
import { t, currentLang } from "./i18n.js";
import { h } from "./ui.js";
import { createRunner, prefersReducedMotion } from "./effect-preview.js";
import { RING, bbox, grow, plan } from "./keyboard-plan.js";

export { RING };

// Frame selection may start on a key: a pointer that moves further than this (px) draws a frame.
const DRAG_PX = 4;

// ── legends ─────────────────────────────────────────────────────────────────────
const NAMES_DE = { Ctrl: "Strg", Caps: "Caps|Lock", Del: "Entf", Ins: "Einfg", Home: "Pos1", End: "Ende", PgUp: "Bild|↑", PgDn: "Bild|↓", PrtSc: "Druck|S-Abf", ScrLk: "Rollen", Pause: "Pause|Untbr", NumLk: "Num" };
const NAMES_EN = { Caps: "Caps|Lock", PrtSc: "Prt|Sc", ScrLk: "Scr|Lk", NumLk: "Num|Lock", PgUp: "Pg|Up", PgDn: "Pg|Dn" };
const GLYPHS = new Set(["⌫", "↵", "⇧", "⊞", "≡", "↑", "↓", "←", "→"]);
const LETTER = /^\p{L}$/u;

/** What a key shows: {glyph} centred, or {lines:[...]} small at the top left, {sub} a second line.
 *  `legends` is the core's table (keyboard.legends): US key name -> [base, shift, altgr]. */
export function legendOf(label, legends, german, isRightAlt, onNumpad) {
  const text = (label || "").trim();
  if (!text) return {};
  if (GLYPHS.has(text)) return { glyph: text };
  if (text === "Alt" && isRightAlt && german) return { lines: ["Alt Gr"] };
  const names = german ? NAMES_DE : NAMES_EN;
  if (names[text]) return { lines: names[text].split("|") };
  if (text === "FN") return { lines: ["Fn"] };
  const lg = !onNumpad && legends ? legends[text] : null;
  if (!lg) return { lines: [text] };
  const [base, shift, alt] = lg;
  // A letter key shows its capital alone; "ß" is a letter too, but its key carries "?" on shift and
  // its capital would be "SS", so it is drawn like a symbol key.
  const up = base.toUpperCase();
  if (LETTER.test(base) && up.length === 1 && (!shift || shift === up)) return { lines: [up], sub: alt || "" };
  return { lines: shift ? [shift] : [], sub: base + (alt ? " " + alt : ""), twoLine: !!shift };
}

// Base Camp draws the Everest Max's keys (and ring LEDs) dark, #1d1d1d, with the face tinted by the
// key's colour. Measured on its picture: a face is 29 + 0.375 x the colour per channel (#00ccb5 ->
// #1d6a61). The Everest 60 shows its colours as they are.
const CAP = 29, TINT = 0.375;
const capFace = (c) => [CAP + c[0] * TINT, CAP + c[1] * TINT, CAP + c[2] * TINT].map(Math.round);

// ── the renderer ────────────────────────────────────────────────────────────────

export function renderKeyboard(container, opts) {
  const o = { mode: "rgb", modules: { numpad: true, dock: true }, selection: [], ...opts };
  const L = o.layout;
  const binding = o.mode === "binding";
  const P = plan(o.model, L, o.modules, { split: !!o.split, ring: o.mode === "rgb", joined: binding });
  const emax = o.model === "everest_max";
  const shade = emax ? capFace : (c) => c;
  const all = [...P.plates, ...P.leds.map((l) => ({ x: l.x, y: l.y, w: l.s, h: l.s })), ...(P.dock ? P.dock.buttons : [])];
  const bb = grow(bbox(all), 4);
  const W = bb.w, H = bb.h;
  const X = (x) => ((x - bb.x) / W * 100) + "%", Y = (y) => ((y - bb.y) / H * 100) + "%";
  const SW = (w) => (w / W * 100) + "%", SH = (hh) => (hh / H * 100) + "%";
  const box = (r) => `left:${X(r.x)};top:${Y(r.y)};width:${SW(r.w)};height:${SH(r.h)}`;

  const kbd = L.keyboard || {};
  // Key names (Strg, Entf, Pos1, Alt Gr) are printed on the keyboard, so they follow its layout,
  // like in Base Camp; the interface language only decides when the layout is not known.
  const german = kbd.id ? kbd.id === "de" : currentLang() === "de";
  let colors = o.colors || null;
  const sel = new Set(o.selection || []);
  const interactive = o.mode === "rgb" && !!(o.onSelect || o.onKey || o.onLed);
  const stage = h("div", { class: "kbf kbf-" + o.mode + " kbf-" + o.model, style: `--u:${100 / W}cqw;aspect-ratio:${W}/${H};max-width:${Math.round(W * (o.model === "everest60" ? 1.1 : 1.45))}px`, role: "group", "aria-label": t("kbf_label") });
  const els = new Map();                 // id -> element
  const centre = new Map();              // id -> [x, y] in canvas units
  const keyInfo = new Map();
  const keyEls = [];
  const bindEls = new Map();             // binding: kid -> element of a key, a D-key or a dock button
  const bindCentre = new Map();          // kid -> [x, y] in canvas units
  const bindable = (el, kid, r) => { el.dataset.kid = kid; bindEls.set(kid, el); bindCentre.set(kid, [r.x + r.w / 2, r.y + r.h / 2]); };

  for (const p of P.plates) stage.append(h("div", { class: "kbf-plate " + p.mod, style: box(p) }));
  for (const w of P.wells) stage.append(h("div", { class: "kbf-well", style: box(w) }));
  // The effect preview paints on this one canvas under the keys: the keys turn see-through
  // (legends and shading stay, they never change) and a frame is ~170 filled rectangles instead
  // of ~170 restyled elements. Measured in the window: the RGB screen went from about a quarter
  // of a CPU core to a few percent.
  const fxCanvas = h("canvas", { class: "kbf-fx", "aria-hidden": "true" });
  stage.append(fxCanvas);
  const fxRects = new Map();             // id -> {x, y, w, h, led} in canvas units

  const mid = {};
  for (const m of ["core", "numpad"]) {
    const ks = P.keys.filter((k) => k.mod === m);
    if (ks.length) { const b = bbox(ks); mid[m] = b.x + b.w / 2; }
  }
  for (const k of P.keys) {
    const el = h("div", { class: "kbf-key" + (k.lit ? " lit" : " nolight") + (binding && k.kid == null ? " nobind" : ""), style: box(k) });
    el.dataset.w = k.w;
    if (binding && k.kid != null) bindable(el, k.kid, k);
    if (k.id != null) { el.dataset.id = k.id; els.set(k.id, el); centre.set(k.id, [k.x + k.w / 2, k.y + k.h / 2]); keyInfo.set(k.id, k);
      fxRects.set(k.id, { x: k.x - bb.x, y: k.y - bb.y, w: k.w, h: k.h, led: false }); }
    keyEls.push([el, k]);
    stage.append(el);
  }
  for (const l of P.leds) {
    const el = h("div", { class: "kbf-led", "data-id": l.id, style: box({ x: l.x, y: l.y, w: l.s, h: l.s }) });
    els.set(l.id, el); centre.set(l.id, [l.x + l.s / 2, l.y + l.s / 2]);
    fxRects.set(l.id, { x: l.x - bb.x, y: l.y - bb.y, w: l.s, h: l.s, led: true });
    stage.append(el);
  }
  const dkEls = P.dkeys.map((d) => {
    const el = h("div", { class: "kbf-key dkey", "data-d": d.idx, style: box(d) }, h("span", { class: "dnum" }, d.label));
    if (binding && d.kid != null) bindable(el, d.kid, d);
    stage.append(el);
    return el;
  });
  let dial = null, dialText = null;
  if (P.dock) {
    const d = P.dock, dl = d.dial;
    stage.append(h("div", { class: "kbf-grip", style: box({ x: d.x + d.w * 0.04, y: d.y + d.h / 2 - d.h * 0.17, w: d.w * 0.05, h: d.h * 0.34 }) }));
    // Binding: the dial is turned with ↺ ↻ above it, as in Base Camp.
    const GLYPH = { prev: "◀◀", next: "▶▶", play: "▶", mute: "×", scroll_l: binding ? "↺" : "◀", scroll_r: binding ? "↻" : "▶" };
    for (const b of d.buttons) {
      const turn = b.name.startsWith("scroll");
      const el = h("div", { class: "kbf-dbtn " + (turn ? (binding ? "turn" : "arrow") : "round"), "data-dock": b.name, style: box(b) }, h("span", null, GLYPH[b.name]));
      if (binding && b.kid != null) bindable(el, b.kid, b);
      stage.append(el);
    }
    if (binding) {
      // "?" above the dock's left end: when the dock's buttons do what they are bound to.
      const sc = d.buttons.find((b) => b.name === "scroll_l") || { y: d.y - 15, w: 12, h: 12 };
      const tip = t("em_dock_help");
      stage.append(h("span", { class: "kbf-dockhelp qhelp", tabindex: "0", role: "note", "aria-label": tip, "data-tip": tip,
        style: box({ x: d.x + d.w * 0.05, y: sc.y, w: sc.w, h: sc.h }) }, "?"));
    }
    if (d.menu) stage.append(h("div", { class: "kbf-dbtn menu", style: box(d.menu) }));
    dialText = binding ? h("span", { class: "kbf-dialtxt" }) : null;
    dial = h("div", { class: "kbf-dial" + (binding ? " screen" : ""), style: box({ x: dl.cx - dl.r, y: dl.cy - dl.r, w: 2 * dl.r, h: 2 * dl.r }), title: t("kbf_dial") }, dialText || h("i"));
    stage.append(dial);
    if (o.onDial) {
      dial.setAttribute("role", "button"); dial.tabIndex = 0;
      dial.addEventListener("click", o.onDial);
      dial.addEventListener("keydown", (ev) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); o.onDial(); } });
    }
  }
  const marquee = h("div", { class: "kbf-marquee", hidden: true });
  stage.append(marquee);

  function legends() {
    for (const [el, k] of keyEls) {
      el.textContent = "";
      el.classList.remove("two", "glyph");
      const right = k.label === "Alt" && k.x > (mid[k.mod] ?? 0);
      const lg = legendOf(k.label, kbd.legends, german, right, k.mod === "numpad");
      if (lg.glyph) { el.classList.add("glyph"); el.append(h("span", { class: "g" }, lg.glyph)); }
      else if (lg.lines || lg.sub) {
        if (lg.twoLine) el.classList.add("two");
        const box2 = h("span", { class: "lg" });
        (lg.lines || []).forEach((s) => box2.append(h("span", null, s)));
        if (lg.sub) box2.append(h("span", { class: "sub" }, lg.sub));
        el.append(box2);
      }
    }
  }

  const luma = (c) => 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2];
  const css = (c) => `rgb(${c[0]},${c[1]},${c[2]})`;
  // What each key shows now: the effect preview repaints every key many times a second, and
  // writing the same colour, class or aria-pressed again still cost style and accessibility work
  // (about half a CPU core on the RGB screen), so only what changed is written.
  const shown = new Map();
  function paint(id) {
    const el = els.get(id);
    if (!el) return;
    const isLed = id >= RING;
    let c = null;
    if (colors) c = isLed ? colors.side && colors.side[id - RING] : colors.leds && colors.leds[id];
    if (c) c = shade(c);
    const prev = shown.get(id) || {};
    const cs = c ? css(c) : null;
    if (cs !== prev.c) {
      if (cs) el.style.setProperty("--c", cs); else el.style.removeProperty("--c");
      el.classList.toggle("on", !!cs);
    }
    const bright = !!c && luma(c) > 140;
    if (bright !== prev.bright) el.classList.toggle("bright", bright);
    const on = sel.has(id);
    if (on !== prev.sel) {
      el.classList.toggle("sel", on);
      if (interactive) el.setAttribute("aria-pressed", on ? "true" : "false");
    }
    shown.set(id, { c: cs, bright, sel: on });
  }
  const paintAll = () => { for (const id of els.keys()) paint(id); };

  const nameOf = (id) => {
    if (id >= RING) return t("em_ring_led", { n: id - RING + 1 });
    const k = keyInfo.get(id);
    return t("em_key_led", { key: k ? (k.label.trim() || t("kbf_space")) : "", n: id });
  };
  let focusId = null;
  const order = [...els.keys()];
  function setRoving(id) {
    const prev = els.get(focusId); if (prev) prev.tabIndex = -1;
    focusId = id;
    const e = els.get(id); if (e) e.tabIndex = 0;
  }
  if (interactive) {
    for (const [id, el] of els) { el.setAttribute("role", "button"); el.setAttribute("aria-label", nameOf(id)); el.tabIndex = -1; }
    if (order.length) setRoving(order[0]);
  }

  // ── selection: click, Ctrl/Shift-click, rubber band, arrows ───────────────────────
  // A disabled id (setDisabled) is drawn dimmed and cannot be selected.
  const disabled = new Set();
  const selectable = (id) => !disabled.has(id);
  const fire = () => { if (o.onSelect) o.onSelect(new Set(sel)); };
  function setSel(ids, add) {
    const old = new Set(sel);
    if (!add) sel.clear();
    ids.forEach((i) => { if (selectable(i)) sel.add(i); });
    for (const i of new Set([...old, ...sel])) paint(i);
    fire();
  }
  function toggle(id) { if (sel.has(id)) sel.delete(id); else sel.add(id); paint(id); fire(); }

  const toCanvas = (ev) => {
    const r = stage.getBoundingClientRect();
    return [bb.x + (ev.clientX - r.left) / r.width * W, bb.y + (ev.clientY - r.top) / r.height * H];
  };
  const idAt = (target) => { const e = target.closest && target.closest("[data-id]"); return e ? Number(e.dataset.id) : null; };
  // A press on empty space starts a frame at once; on a key it is a click, unless the pointer then
  // moves further than DRAG_PX: a frame may start on a key, as in Base Camp.
  let drag = null;
  function frameTo(ev) {
    const [px, py] = toCanvas(ev);
    const x = Math.min(px, drag.x), y = Math.min(py, drag.y), w = Math.abs(px - drag.x), hh = Math.abs(py - drag.y);
    marquee.hidden = false;
    marquee.style.cssText = box({ x, y, w, h: hh });
    const next = new Set(drag.base);
    for (const [id, [cx, cy]] of centre) if (selectable(id) && cx >= x && cx <= x + w && cy >= y && cy <= y + hh) next.add(id);
    if (next.size === sel.size && [...next].every((i) => sel.has(i))) return;
    const old = new Set(sel);
    sel.clear(); next.forEach((i) => sel.add(i));
    for (const i of new Set([...old, ...sel])) paint(i);
    fire();
  }
  if (interactive) {
    stage.addEventListener("pointerdown", (ev) => {
      if (ev.button !== 0) return;
      const id = idAt(ev.target), add = ev.ctrlKey || ev.metaKey || ev.shiftKey;
      const [x, y] = toCanvas(ev);
      drag = { x, y, add, id, cx: ev.clientX, cy: ev.clientY, frame: id == null, base: add ? new Set(sel) : new Set() };
      if (id == null && !add) setSel([], false);
      stage.setPointerCapture(ev.pointerId);
    });
    stage.addEventListener("pointermove", (ev) => {
      if (!drag) return;
      if (!drag.frame) {
        if (Math.hypot(ev.clientX - drag.cx, ev.clientY - drag.cy) <= DRAG_PX) return;
        drag.frame = true;
      }
      frameTo(ev);
    });
    stage.addEventListener("pointerup", (ev) => {
      const d = drag;
      drag = null; marquee.hidden = true;
      if (!d || d.frame || d.id == null) return;
      const id = d.id;
      setRoving(id);
      if (!selectable(id)) return;
      if (id >= RING && o.onLed) o.onLed(id - RING, ev);
      if (id < RING && o.onKey) o.onKey(id, ev);
      if (d.add) toggle(id); else setSel([id], false);
    });
    stage.addEventListener("pointercancel", () => { drag = null; marquee.hidden = true; });
    stage.addEventListener("keydown", (ev) => {
      const id = idAt(ev.target);
      if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === "a") { ev.preventDefault(); setSel(order, false); return; }
      if (ev.key === "Escape") { setSel([], false); return; }
      if (id == null) return;
      if (ev.key === " " || ev.key === "Enter") {
        ev.preventDefault();
        if (!selectable(id)) return;
        if (ev.ctrlKey || ev.shiftKey) toggle(id); else setSel([id], false);
        return;
      }
      const dir = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] }[ev.key];
      if (!dir) return;
      ev.preventDefault();
      const [cx, cy] = centre.get(id);
      let best = null, bd = Infinity;
      for (const [other, [x, y]] of centre) {
        if (other === id) continue;
        const dx = x - cx, dy = y - cy, along = dx * dir[0] + dy * dir[1];
        if (along <= 2) continue;
        const dist = along + Math.abs(dx * dir[1] - dy * dir[0]) * 2.5;
        if (dist < bd) { bd = dist; best = other; }
      }
      if (best == null) return;
      setRoving(best); els.get(best).focus();
      if (ev.shiftKey) { [id, best].filter(selectable).forEach((i) => { sel.add(i); paint(i); }); fire(); }
    });
  }

  // ── D-keys: the pictures they show ─────────────────────────────────────────────
  let dstate = [];
  function drawDKeys() {
    dkEls.forEach((el, i) => {
      const s = dstate.find((x) => x.idx === i) || {};
      el.style.backgroundImage = "";
      el.classList.remove("haspic");
      el.querySelector(".dtxt")?.remove();
      if (!binding) el.setAttribute("aria-label", s.label || "D" + (i + 1));
      if (s.text) el.append(h("span", { class: "dtxt" }, s.text));
      if (s.thumb) s.thumb.then((url) => { if (url && el.isConnected && dstate.find((x) => x.idx === i) === s) { el.style.backgroundImage = `url("${url}")`; el.classList.add("haspic"); } });
    });
  }

  // ── key binding: every key a button ─────────────────────────────────────────────
  let bstate = { states: {}, selected: null, labels: {} };
  let bindFocus = null;
  function setBindFocus(kid) {
    const prev = bindEls.get(bindFocus); if (prev) prev.tabIndex = -1;
    bindFocus = kid;
    const el = bindEls.get(kid); if (el) el.tabIndex = 0;
  }
  function drawBindings() {
    if (!binding) return;
    for (const [kid, el] of bindEls) {
      const st = bstate.states[kid] || "";
      el.classList.toggle("st-bound", st === "bound");
      el.classList.toggle("st-off", st === "off");
      el.classList.toggle("st-sel", kid === bstate.selected);
      el.setAttribute("aria-pressed", kid === bstate.selected ? "true" : "false");
      const label = bstate.labels[kid];
      if (label) { el.title = label; el.setAttribute("aria-label", label); }
    }
    setBindFocus(bindEls.has(bstate.selected) ? bstate.selected : bindFocus ?? bindEls.keys().next().value);
  }
  if (binding && o.onBind) {
    for (const el of bindEls.values()) { el.setAttribute("role", "button"); el.tabIndex = -1; }
    stage.addEventListener("click", (ev) => {
      const el = ev.target.closest && ev.target.closest("[data-kid]");
      if (el && stage.contains(el)) { setBindFocus(Number(el.dataset.kid)); o.onBind(Number(el.dataset.kid)); }
    });
    stage.addEventListener("keydown", (ev) => {
      const el = ev.target.closest && ev.target.closest("[data-kid]");
      if (!el) return;
      const kid = Number(el.dataset.kid);
      if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); o.onBind(kid); return; }
      const dir = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] }[ev.key];
      if (!dir) return;
      ev.preventDefault();
      const [cx, cy] = bindCentre.get(kid);
      let best = null, bd = Infinity;
      for (const [other, [x, y]] of bindCentre) {
        const dx = x - cx, dy = y - cy, along = dx * dir[0] + dy * dir[1];
        if (other === kid || along <= 2) continue;
        const dist = along + Math.abs(dx * dir[1] - dy * dir[0]) * 2.5;
        if (dist < bd) { bd = dist; best = other; }
      }
      if (best == null) return;
      setBindFocus(best); bindEls.get(best).focus();
    });
  }

  // ── effect preview ───────────────────────────────────────────────────────────────
  /** spec: {effect, kind: "single"|"dual"|"rainbow", c1, c2, speed, direction, brightness}; colours are [r,g,b].
   *  Starts (or updates) the animation of effect-preview.js; stopEffect() ends it. */
  const fxIds = [...centre.keys()];
  const fxPoints = fxIds.map((id) => {
    const [cx, cy] = centre.get(id);
    return { x: Math.max(0, Math.min(1, (cx - bb.x) / W)), y: Math.max(0, Math.min(1, (cy - bb.y) / H)), aspect: W / H };
  });
  // The canvas follows the picture's size (it scales with the window) in device pixels.
  let fxCtx = null, fxScale = 1, fxLast = null;
  function fxResize() {
    const r = stage.getBoundingClientRect();
    if (!r.width) return false;
    const dpr = window.devicePixelRatio || 1;
    const w = Math.round(r.width * dpr), hh = Math.round(r.height * dpr);
    if (fxCanvas.width !== w || fxCanvas.height !== hh) { fxCanvas.width = w; fxCanvas.height = hh; }
    fxScale = w / W;
    if (!fxCtx) fxCtx = fxCanvas.getContext("2d", { alpha: true });
    return !!fxCtx;
  }
  function fxDraw(cols) {
    fxLast = cols;
    if (!fxCtx && !fxResize()) return;
    const c = fxCtx, s = fxScale;
    // Corner radii as the CSS draws keys and ring LEDs (app.css, .kbf-everest_max).
    const keyR = emax ? 2.2 * s : 3 * (window.devicePixelRatio || 1), ledR = emax ? 2 * s : 0;
    c.clearRect(0, 0, fxCanvas.width, fxCanvas.height);
    for (let i = 0; i < fxIds.length; i++) {
      const r = fxRects.get(fxIds[i]);
      if (!r || !cols[i]) continue;
      const col = shade(cols[i]);
      c.fillStyle = `rgb(${col[0]},${col[1]},${col[2]})`;
      if (r.led && !ledR) c.fillRect(r.x * s, r.y * s, r.w * s, r.h * s);
      else { c.beginPath(); c.roundRect(r.x * s, r.y * s, r.w * s, r.h * s, r.led ? ledR : keyR); c.fill(); }
    }
  }
  const fxObserver = typeof ResizeObserver === "function"
    ? new ResizeObserver(() => { if (stage.classList.contains("fx") && fxResize() && fxLast) fxDraw(fxLast); }) : null;
  if (fxObserver) fxObserver.observe(stage);
  const fxRunner = createRunner({
    points: fxPoints, reduced: prefersReducedMotion, isConnected: () => stage.isConnected,
    paint(cols) {
      if (!stage.classList.contains("fx")) { stage.classList.add("fx"); fxResize(); }
      fxDraw(cols);
    },
  });
  function setEffect(spec) {
    const s = { ...spec };
    if (!s.effect) s.effect = s.kind === "off" ? "off" : "static";      // older callers: a still colour picture
    fxRunner.update(s);
  }
  /** Effects per key (Individuell): Map id -> spec as setEffect takes it; an id left out stays dark.
   *  Keys sharing a spec object run as one effect (effect-preview.js keyFrame). */
  function setKeyEffects(map) {
    fxRunner.update({ keys: fxIds.map((id) => map.get(id) || null) });
  }
  function stopEffect() {
    fxRunner.stop();
    stage.classList.remove("fx");
    if (fxCtx) fxCtx.clearRect(0, 0, fxCanvas.width, fxCanvas.height);
    fxLast = null;
  }

  const api = {
    el: stage,
    ids: () => [...order],
    setColors(c) { colors = c; paintAll(); },
    setSelection(ids) { sel.clear(); [...ids].forEach((i) => sel.add(i)); paintAll(); },
    getSelection: () => new Set(sel),
    setDKeys(list) { dstate = list || []; drawDKeys(); },
    /** What the dial says it is (e.g. the display mode it shows); the binding picture shows `short` on it. */
    setDialLabel(text, short) {
      if (!dial) return;
      dial.title = text; dial.setAttribute("aria-label", text);
      if (dialText) dialText.textContent = short ?? text;
    },
    /** Binding: {states: {kid: "bound" | "off"}, selected: kid or null, labels: {kid: text}}. */
    setBindings(b) { bstate = { states: {}, selected: null, labels: {}, ...b }; drawBindings(); },
    /** Ids that cannot be selected now (dimmed, aria-disabled); they leave the selection without an onSelect. */
    setDisabled(ids) {
      for (const id of disabled) { const e = els.get(id); if (e) { e.classList.remove("dis"); e.removeAttribute("aria-disabled"); } }
      disabled.clear();
      for (const id of ids) {
        const e = els.get(id);
        if (!e) continue;
        disabled.add(id);
        e.classList.add("dis");
        e.setAttribute("aria-disabled", "true");
        if (sel.delete(id)) paint(id);
      }
    },
    setEffect, setKeyEffects, stopEffect,
    focusKey: (id) => { const e = els.get(id); if (e) e.focus(); },
  };
  legends(); paintAll(); drawDKeys(); drawBindings();
  container.append(stage);
  return api;
}

/** The legend under the key-binding picture: Standard, Selected, Assigned, Disabled. */
export function legendBar() {
  const item = (cls, key) => h("span", { class: "kbf-li" }, h("i", { class: "sw " + cls }), t(key));
  return h("div", { class: "kbf-legend" }, item("std", "kbf_legend_standard"), item("sel", "kbf_legend_selected"),
    item("bound", "kbf_legend_assigned"), item("off", "kbf_legend_disabled"));
}
