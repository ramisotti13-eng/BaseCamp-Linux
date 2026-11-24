// Pieces the Einstellungen pages of the Everest Max and the Makalu share: the panel head with its "?" help, the three
// buttons at the bottom (factory reset, Quick Start, manuals), a slider with named stops, and a write that waits for
// 300 ms of quiet like the RGB pages do.
import bc from "./bridge.js";
import { t } from "./i18n.js";
import { h, confirmDialog } from "./ui.js";

const REPO = "https://github.com/ramisotti13-eng/BaseCamp-Linux";
// The project's own README and docs folder; Mountain's manuals and wizard are not ours to link.
export const QUICK_START_URL = REPO + "#readme";
export const MANUALS_URL = REPO + "/tree/HEAD/docs";

export const WRITE_MS = 300;

/** A small "?" whose text shows as a tooltip (and is read out): Base Camp's (?) beside a heading. */
export function helpDot(text) {
  return h("span", { class: "qmark", tabindex: "0", role: "img", title: text, "aria-label": t("set_help") + " " + text }, "?");
}

/** A panel head: the blue accent, the heading and, when given, its "?". */
export function panelHead(title, help) {
  return h("div", { class: "head" }, h("span", { class: "acc" }),
    h("h2", { class: "t h", style: "margin:0;font-weight:400" }, title), help ? helpDot(help) : null);
}

/** The bottom row of full-width buttons. `onReset()` runs after the person confirmed `resetMessage`. */
export function bottomButtons({ manualsLabel, resetMessage, onReset }) {
  const reset = h("button", { class: "btn danger", type: "button", id: "set-factory", onclick: async () => {
    if (!(await confirmDialog({ title: t("set_reset_title"), message: resetMessage, okLabel: t("set_reset_btn"), danger: true }))) return;
    await onReset();
  } }, t("set_reset_btn"));
  const link = (id, label, url) => h("button", { class: "btn", type: "button", id, onclick: () => bc.openExternal(url) }, label);
  return h("div", { class: "setbottom" }, reset,
    link("set-quick", t("set_quick_btn"), QUICK_START_URL), link("set-manuals", manualsLabel, MANUALS_URL));
}

/** `fn` runs 300 ms after the last call.flush() runs it at once when one is waiting (the screen closes). */
export function quietWrite(fn, ms = WRITE_MS) {
  let timer = null;
  const run = () => { timer = null; fn(); };
  return {
    call() { clearTimeout(timer); timer = setTimeout(run, ms); },
    flush() { if (timer != null) { clearTimeout(timer); run(); } },
    get waiting() { return timer != null; },
  };
}

/** A slider over named stops (Base Camp's polling rate and button response time). `stops` are the values, `fmt` shows
 *  one. Returns {el, set(value), get()}; `onInput(value)` fires while dragging. */
export function stopSlider(id, stops, value, { fmt = String, onInput, label } = {}) {
  const idx = (v) => Math.max(0, stops.indexOf(v));
  const inp = h("input", { type: "range", id, min: 0, max: stops.length - 1, step: 1, value: idx(value), "aria-label": label || null });
  const out = h("output", { for: id }, fmt(stops[idx(value)]));
  const ticks = h("div", { class: "stoptick", "aria-hidden": "true" }, stops.map((v) => h("span", null, String(v))));
  inp.addEventListener("input", () => {
    const v = stops[Number(inp.value)];
    out.textContent = fmt(v);
    if (onInput) onInput(v);
  });
  return {
    el: h("div", { class: "stopslider" }, h("div", { class: "range-row" }, inp, out), ticks),
    get: () => stops[Number(inp.value)],
    set: (v) => { inp.value = idx(v); out.textContent = fmt(stops[idx(v)]); },
  };
}
