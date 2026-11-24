// One colour popup for the whole app, modelled on Base Camp's: a saturation/value field with a round
// handle, a hue strip, HEX / R / G / B fields and "Saved colours" shared by every colour field.
//
//   const sw = colorInput({ id, class, value: "#rrggbb", disabled, "aria-label": "...", title });
//   sw.value            "#rrggbb"; settable from code (fires no event)
//   sw.addEventListener("input", ...)   while the user drags or types
//   sw.addEventListener("change", ...)  when a value is settled (handle released, field committed,
//                                       saved colour clicked)
//
// A drop-in for <input type="color">. The saved colours live in the app's configuration
// (settings.saved_colors / settings.set_saved_colors) and are loaded when the popup first opens.
import bc from "./bridge.js";
import { t } from "./i18n.js";
import { h, icon } from "./ui.js";

const DEFAULT_SAVED = ["#5ccf5c", "#8fdc4a", "#a6e03c", "#a03c8c", "#e04632", "#e032a0", "#00c8b4", "#0a78c8", "#5a14c8", "#d2145a"];
const MAX_SAVED = 64;

// ── colour maths ───────────────────────────────────────────────────────────
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

export function normHex(s) {
  let x = String(s == null ? "" : s).trim().toLowerCase().replace(/^#/, "");
  if (/^[0-9a-f]{3}$/.test(x)) x = x.replace(/./g, (c) => c + c);
  return /^[0-9a-f]{6}$/.test(x) ? "#" + x : null;
}

export function hexToRgb(hex) {
  const n = parseInt((normHex(hex) || "#000000").slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

export function rgbToHex(r, g, b) {
  return "#" + [r, g, b].map((v) => clamp(Math.round(Number(v) || 0), 0, 255).toString(16).padStart(2, "0")).join("");
}

/** h 0..360, s and v 0..1 -> [r, g, b] 0..255 */
export function hsvToRgb(hh, s, v) {
  const f = (n) => { const k = (n + hh / 60) % 6; return v - v * s * Math.max(0, Math.min(k, 4 - k, 1)); };
  return [f(5) * 255, f(3) * 255, f(1) * 255].map(Math.round);
}

/** [r, g, b] -> {h, s, v}; h is 0 for a grey */
export function rgbToHsv(r, g, b) {
  r /= 255; g /= 255; b /= 255;
  const mx = Math.max(r, g, b), mn = Math.min(r, g, b), d = mx - mn;
  let hh = 0;
  if (d) {
    if (mx === r) hh = ((g - b) / d) % 6;
    else if (mx === g) hh = (b - r) / d + 2;
    else hh = (r - g) / d + 4;
    hh = (hh * 60 + 360) % 360;
  }
  return { h: hh, s: mx ? d / mx : 0, v: mx };
}

// ── saved colours: shared by every field, kept in the app's configuration ──
let saved = DEFAULT_SAVED.slice();
let savedLoaded = false;
const subscribers = new Set();

function setSaved(list) {
  saved = list.map(normHex).filter(Boolean);
  for (const fn of [...subscribers]) fn();
}

async function loadSaved() {
  if (savedLoaded) return;
  savedLoaded = true;
  const r = await bc.call("settings.saved_colors");
  if (r && r.ok && Array.isArray(r.colors)) setSaved(r.colors);
  bc.on("settings.saved_colors_changed", (_t, p) => { if (p && Array.isArray(p.colors)) setSaved(p.colors); });
}

async function storeSaved(list) {
  setSaved(list);
  await bc.call("settings.set_saved_colors", { colors: list });
}

// ── the popup (one for the app) ────────────────────────────────────────────
let pop = null; // the open popup's state, or null

function fire(el, type) { el.dispatchEvent(new Event(type, { bubbles: true })); }

function closePopup(restore) {
  if (!pop) return;
  const p = pop;
  pop = null;
  window.removeEventListener("keydown", p.onKey, true);
  window.removeEventListener("pointerdown", p.onOutside, true);
  window.removeEventListener("resize", p.place);
  p.panel.destroy();
  p.el.remove();
  if (restore && p.owner.value !== p.opened) { p.owner.__setValue(p.opened); fire(p.owner, "input"); fire(p.owner, "change"); }
  p.owner.setAttribute("aria-expanded", "false");
  p.owner.focus();
}

/** The picker itself (field, hue strip, HEX/R/G/B, saved colours), to embed in a panel or to put in a popup.
 *  onInput(hex) while the user drags or types, onChange(hex) when a value is settled. Returns
 *  {el, value (get/set; setting fires nothing), focus(), destroy()}; call destroy() when the element goes away. */
export function colorPanel({ value = "#000000", onInput, onChange } = {}) {
  let { h: hue, s: sat, v: val } = rgbToHsv(...hexToRgb(value));
  const emitInput = () => { if (onInput) onInput(cur()); };
  const emitChange = () => { if (onChange) onChange(cur()); };
  let chosen = -1; // index in `saved` of the saved colour picked last
  const cur = () => rgbToHex(...hsvToRgb(hue, sat, val));

  const handle = h("div", { class: "cp-handle" });
  const field = h("div", { class: "cp-sv", tabindex: "0", role: "slider", "aria-label": t("cp_field") }, handle);
  const hueHandle = h("div", { class: "cp-hhandle" });
  const strip = h("div", { class: "cp-hue", tabindex: "0", role: "slider", "aria-label": t("cp_hue"), "aria-valuemin": "0", "aria-valuemax": "360" }, hueHandle);
  const mkNum = (label) => h("input", { class: "cp-num", type: "text", inputmode: "numeric", maxlength: "3", "aria-label": label, autocomplete: "off", spellcheck: "false" });
  const hexIn = h("input", { class: "cp-hex", type: "text", maxlength: "7", "aria-label": "HEX", autocomplete: "off", spellcheck: "false" });
  const rIn = mkNum("R"), gIn = mkNum("G"), bIn = mkNum("B");
  const cap = (s) => h("span", null, s);
  const addBtn = h("button", { type: "button", class: "cp-ibtn", title: t("cp_save"), "aria-label": t("cp_save") }, icon("plus"));
  const delBtn = h("button", { type: "button", class: "cp-ibtn", title: t("cp_remove"), "aria-label": t("cp_remove") }, icon("trash"));
  const grid = h("div", { class: "cp-saved" });
  const el = h("div", { class: "cp-panel", role: "group", "aria-label": t("cp_title") },
    h("div", { class: "cp-main" }, field, strip),
    h("div", { class: "cp-fields" }, hexIn, rIn, gIn, bIn),
    h("div", { class: "cp-caps" }, cap("HEX"), cap("R"), cap("G"), cap("B")),
    h("div", { class: "cp-savedhead" }, h("span", { class: "cp-savedtitle" }, t("cp_saved")), addBtn, delBtn),
    grid);

  const selectedSaved = () => (chosen >= 0 && chosen < saved.length ? chosen : saved.indexOf(cur()));

  function drawSaved() {
    grid.replaceChildren(...saved.map((c, i) => h("button", { type: "button", class: "cp-chip" + (i === chosen ? " on" : ""), style: "background:" + c,
      title: c, "aria-label": c, "aria-pressed": i === chosen ? "true" : "false",
      onclick: () => { chosen = i; apply(c, true); drawSaved(); } })));
    delBtn.disabled = selectedSaved() < 0;
  }

  /** Paint everything from hue/sat/val and hand the value to the swatch; `skip` is the text field being typed in. */
  function paint(skip) {
    const hex = cur(), [r, g, b] = hsvToRgb(hue, sat, val);
    field.style.setProperty("--hue", "hsl(" + hue + " 100% 50%)");
    handle.style.left = sat * 100 + "%";
    handle.style.top = (1 - val) * 100 + "%";
    handle.style.background = hex;
    hueHandle.style.top = (hue / 360) * 100 + "%";
    hueHandle.style.background = "hsl(" + hue + " 100% 50%)";
    field.setAttribute("aria-valuetext", Math.round(sat * 100) + " %, " + Math.round(val * 100) + " %");
    strip.setAttribute("aria-valuenow", String(Math.round(hue)));
    if (skip !== hexIn) hexIn.value = hex;
    if (skip !== rIn) rIn.value = r;
    if (skip !== gIn) gIn.value = g;
    if (skip !== bIn) bIn.value = b;
    delBtn.disabled = selectedSaved() < 0;
  }

  /** Take a colour (from a field or a saved chip) and tell the owner. */
  function apply(hex, settled, skip) {
    const hsv = rgbToHsv(...hexToRgb(hex));
    sat = hsv.s; val = hsv.v;
    if (hsv.s > 0 && hsv.v > 0) hue = hsv.h; // a grey keeps the hue the user had
    paint(skip);
    emitInput();
    if (settled) emitChange();
  }

  /** The handle moved by hand: no saved colour is the chosen one any more. */
  function moved() { chosen = -1; drawSaved(); paint(); emitInput(); }

  function drag(target, onMove) {
    target.addEventListener("pointerdown", (e) => {
      if (e.button !== 0) return;
      e.preventDefault();
      target.focus();
      target.setPointerCapture(e.pointerId);
      const go = (ev) => {
        const r = target.getBoundingClientRect();
        onMove(clamp((ev.clientX - r.left) / r.width, 0, 1), clamp((ev.clientY - r.top) / r.height, 0, 1));
        moved();
      };
      const up = () => {
        target.removeEventListener("pointermove", go);
        target.removeEventListener("pointerup", up);
        target.removeEventListener("pointercancel", up);
        emitChange();
      };
      target.addEventListener("pointermove", go);
      target.addEventListener("pointerup", up);
      target.addEventListener("pointercancel", up);
      go(e);
    });
  }
  drag(field, (x, y) => { sat = x; val = 1 - y; });
  drag(strip, (_x, y) => { hue = y * 360; });

  const arrows = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, 1], ArrowDown: [0, -1] };
  field.addEventListener("keydown", (e) => {
    const a = arrows[e.key];
    if (!a) return;
    e.preventDefault();
    const step = e.shiftKey ? 0.1 : 0.01;
    sat = clamp(sat + a[0] * step, 0, 1); val = clamp(val + a[1] * step, 0, 1);
    moved(); emitChange();
  });
  strip.addEventListener("keydown", (e) => {
    const d = { ArrowUp: -1, ArrowLeft: -1, ArrowDown: 1, ArrowRight: 1 }[e.key];
    if (!d) return;
    e.preventDefault();
    hue = clamp(hue + d * (e.shiftKey ? 10 : 1), 0, 360);
    moved(); emitChange();
  });

  // Fields: valid text goes through while typing (input), commit on Enter / leaving (change).
  hexIn.addEventListener("input", () => {
    if (/^#?[0-9a-f]{6}$/i.test(hexIn.value.trim())) { chosen = -1; apply(normHex(hexIn.value), false, hexIn); drawSaved(); }
  });
  for (const i of [rIn, gIn, bIn]) {
    i.addEventListener("input", () => {
      const n = [rIn, gIn, bIn].map((x) => x.value.trim());
      if (n.every((s) => /^\d{1,3}$/.test(s))) { chosen = -1; apply(rgbToHex(...n.map(Number)), false, i); drawSaved(); }
    });
  }
  const settle = () => { paint(); emitChange(); };
  for (const i of [hexIn, rIn, gIn, bIn]) {
    i.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); settle(); i.select(); } });
    i.addEventListener("change", settle);
    i.addEventListener("focus", () => i.select());
  }

  addBtn.addEventListener("click", () => {
    const c = cur(), i = saved.indexOf(c);
    if (i >= 0) { chosen = i; drawSaved(); return; }
    storeSaved([...saved, c].slice(-MAX_SAVED));
    chosen = saved.indexOf(c);
    drawSaved();
  });
  delBtn.addEventListener("click", () => {
    const i = selectedSaved();
    if (i < 0) return;
    chosen = -1;
    storeSaved(saved.filter((_, k) => k !== i));
    drawSaved();
    addBtn.focus();
  });

  paint();
  drawSaved();
  subscribers.add(drawSaved);
  loadSaved();
  return {
    el,
    get value() { return cur(); },
    set value(v) {
      const hex = normHex(v);
      if (!hex || hex === cur()) return;
      const hsv = rgbToHsv(...hexToRgb(hex));
      sat = hsv.s; val = hsv.v;
      if (hsv.s > 0 && hsv.v > 0) hue = hsv.h;
      chosen = -1; paint(); drawSaved();
    },
    focus() { field.focus(); },
    destroy() { subscribers.delete(drawSaved); },
  };
}

function openPopup(owner) {
  if (pop) { const same = pop.owner === owner; closePopup(false); if (same) return; }
  const opened = owner.value;
  const panel = colorPanel({
    value: opened,
    onInput: (hex) => { owner.__setValue(hex); fire(owner, "input"); },
    onChange: (hex) => { owner.__setValue(hex); fire(owner, "change"); },
  });
  const el = panel.el;
  el.classList.add("cp-pop");
  el.setAttribute("role", "dialog");

  // Tab stays inside, Esc restores and closes.
  const onKey = (e) => {
    if (e.key === "Escape") { e.preventDefault(); e.stopImmediatePropagation(); closePopup(true); return; }
    if (e.key === "Tab" && el.contains(document.activeElement)) {
      const f = [...el.querySelectorAll("button, input, [tabindex='0']")].filter((x) => !x.disabled);
      const first = f[0], last = f[f.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    }
  };
  const onOutside = (e) => { if (!el.contains(e.target) && !owner.contains(e.target)) closePopup(false); };
  const place = () => {
    const m = 8, r = owner.getBoundingClientRect(), w = el.offsetWidth, hh = el.offsetHeight;
    let top = r.bottom + 6;
    if (top + hh > innerHeight - m) top = r.top - hh - 6;
    if (top < m) top = Math.max(m, innerHeight - hh - m);
    el.style.left = clamp(r.left, m, Math.max(m, innerWidth - w - m)) + "px";
    el.style.top = top + "px";
  };

  pop = { el, owner, opened, onKey, onOutside, place, panel };
  window.addEventListener("keydown", onKey, true);
  window.addEventListener("pointerdown", onOutside, true);
  window.addEventListener("resize", place);
  document.body.append(el);
  owner.setAttribute("aria-expanded", "true");
  place();
  panel.focus();
}

// ── the swatch ─────────────────────────────────────────────────────────────

/** A colour swatch that opens the popup. attrs as for h(): id, class, disabled, title, aria-label, value. */
export function colorInput(attrs = {}) {
  const { value, class: cls, ...rest } = attrs;
  const fill = h("span", { class: "cp-fill" });
  const el = h("button", { type: "button", ...rest, class: "cpswatch" + (cls ? " " + cls : ""), "aria-haspopup": "dialog", "aria-expanded": "false" }, fill);
  let cur = "#000000";
  const set = (v) => { cur = normHex(v) || cur; fill.style.background = cur; };
  Object.defineProperty(el, "value", { get: () => cur, set, configurable: true });
  el.__setValue = set;
  set(value || "#000000");
  el.addEventListener("click", () => { if (!el.disabled) openPopup(el); });
  return el;
}
