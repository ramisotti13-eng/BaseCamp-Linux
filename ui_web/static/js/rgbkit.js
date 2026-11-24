// Small RGB controls shared by the Everest 60 and MacroPad screens: colour field, slider row,
// segmented choice. Colours travel as [r, g, b] (0..255), the input speaks "#rrggbb".
import { h } from "./ui.js";
import { colorInput } from "./colorpicker.js";

export const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

export function toHex(rgb) {
  const c = Array.isArray(rgb) ? rgb : [0, 0, 0];
  return "#" + c.map((v) => clamp(Math.round(Number(v) || 0), 0, 255).toString(16).padStart(2, "0")).join("");
}

export function fromHex(hex) {
  const m = /^#?([0-9a-f]{6})$/i.exec(String(hex).trim());
  if (!m) return [0, 0, 0];
  const n = parseInt(m[1], 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

/** A colour input with its hex text. `onInput(rgb)` fires while picking, `onChange(rgb)` when done. */
export function colorField(id, rgb, { onInput, onChange, label } = {}) {
  const inp = colorInput({ class: "rgbsw", id, value: toHex(rgb), "aria-label": label || null });
  const out = h("output", { class: "rgbhex", for: id }, toHex(rgb));
  inp.addEventListener("input", () => { out.textContent = inp.value; if (onInput) onInput(fromHex(inp.value)); });
  inp.addEventListener("change", () => { if (onChange) onChange(fromHex(inp.value)); });
  return {
    el: h("span", { class: "rgbcf" }, inp, out),
    input: inp,
    get: () => fromHex(inp.value),
    set: (c) => { inp.value = toHex(c); out.textContent = inp.value; },
  };
}

/** A labelled range with its value. Returns {el, input, get, set}. `fmt(v)` formats the output. */
export function rangeRow(id, value, { min = 0, max = 100, step = 1, fmt = (v) => v + " %", onInput, onChange } = {}) {
  const inp = h("input", { type: "range", id, min, max, step, value });
  const out = h("output", { for: id }, fmt(value));
  inp.addEventListener("input", () => { out.textContent = fmt(Number(inp.value)); if (onInput) onInput(Number(inp.value)); });
  inp.addEventListener("change", () => { if (onChange) onChange(Number(inp.value)); });
  return {
    el: h("div", { class: "range-row" }, inp, out),
    input: inp,
    get: () => Number(inp.value),
    set: (v) => { inp.value = v; out.textContent = fmt(Number(v)); },
  };
}

/** A segmented choice: options [{value, label}]; `onChange(value)`. Returns {el, get, set}. */
export function segmented(label, options, value, onChange) {
  let cur = value;
  const btns = options.map((o) => h("button", { type: "button", class: "rgbsegbtn" + (String(o.value) === String(cur) ? " on" : ""),
    "aria-pressed": String(o.value) === String(cur) ? "true" : "false", onclick: () => set(o.value, true) }, o.label));
  const el = h("div", { class: "rgbseg", role: "group", "aria-label": label }, btns);
  function set(v, fire) {
    cur = v;
    options.forEach((o, i) => {
      const on = String(o.value) === String(v);
      btns[i].classList.toggle("on", on);
      btns[i].setAttribute("aria-pressed", on ? "true" : "false");
    });
    if (fire && onChange) onChange(v);
  }
  return { el, get: () => cur, set: (v) => set(v, false) };
}

/** A field with its label on top. */
export function field(label, control, forId, extra) {
  return h("div", extra ? { class: extra } : null, h("label", { class: "lab", for: forId || null }, label), control);
}
