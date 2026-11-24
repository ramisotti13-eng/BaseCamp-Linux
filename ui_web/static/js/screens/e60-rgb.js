// Everest 60, RGB: the effect list, the controls of the chosen effect, the side ring, and for
// Custom the per-key editor.
import bc from "../bridge.js";
import { t, currentLang } from "../i18n.js";
import { renderKeyboard } from "../keyboard-flat.js";
import { h, icon, clear, toast, makeScope } from "../ui.js";
import { colorField, rangeRow, field } from "../rgbkit.js";
import { frame, statusBar, watchApplied } from "./e60-common.js";
import { buildPerKey } from "./e60-perkey.js";

// The same effect signs as the Everest Max's effect list (symbols in index.html).
const E60_FX_ICON = { static: "sun", breathing: "breath", wave: "wave", tornado: "tornado",
  reactive: "bolt", matrix: "matrix", yeti: "paw", custom: "brush", off: "bulb-off" };

let scope = null;

const MODE_KEYS = { single: "rgb_cmode_single", dual: "rgb_cmode_dual", rainbow: "rgb_cmode_rainbow" };

/** The effect's name: the same words as on the Everest Max (Base Camp's names), else a MacroPad
 *  translation, else the controller's own name. */
function effectLabel(e) {
  for (const key of ["em_fx_" + e.id, "mp_effect_" + e.id]) {
    const s = t(key);
    if (s !== key) return s;
  }
  return e.name;
}

export async function mount(el) {
  scope = makeScope();
  const fr = frame(el, "rgb", true);
  const S = { effects: [], dirs: {}, s: null, ring: { color: [255, 0, 67], brightness: 100 }, perkey: null, layout: null, kb: null };

  scope.add(() => { if (S.kb) S.kb.stopEffect(); });                 // no frames after the screen is gone
  const sb = statusBar(scope);
  watchApplied(scope);
  const heroHost = h("div", { class: "e60hero" });
  const ctlHost = h("div", { class: "e60ctl" });
  const ringHost = h("div", { class: "e60ring" });
  const keyHost = h("div", { class: "e60hero e60keys" });
  // The effect and the side ring side by side under the picture: stacked, the screen needed scrolling.
  // Custom: its keyboard takes the picture's place, its tools the effect panel's.
  fr.main.append(sb.el, heroHost, keyHost, h("div", { class: "e60row" }, ctlHost, ringHost));

  const spec = (id) => S.effects.find((e) => e.id === id) || S.effects[0];

  // ── the effect list ─────────────────────────────────────────────────────
  function renderList() {
    const col = fr.col;
    clear(col);
    col.append(h("div", { class: "head" }, h("span", { class: "acc" }), h("h2", { class: "t h", style: "margin:0;font-weight:400" }, t("e60_effects_title"))));
    const rows = h("div", { class: "rows" });
    for (const e of S.effects) {
      const on = S.s && S.s.effect === e.id;
      const sub = e.color_modes.length ? e.color_modes.map((m) => t(MODE_KEYS[m])).join(" · ") : (e.id === "custom" ? t("e60_effect_custom_sub") : "");
      rows.append(h("div", { class: "row" + (on ? " on" : "") },
        h("button", { class: "pick", type: "button", "aria-pressed": on ? "true" : "false", "data-f": "fx-" + e.id, onclick: () => choose(e.id) },
          h("div", { class: "tile rgbfx" }, icon(E60_FX_ICON[e.id] || "bulb")),
          h("div", { class: "txt" }, h("div", { class: "nm h" }, effectLabel(e)), h("div", { class: "sub" }, sub)))));
    }
    col.append(rows);
  }

  async function choose(id) {
    const keep = document.activeElement && document.activeElement.getAttribute("data-f");
    S.s.effect = id;
    if (id === "custom") {
      // Custom has no Apply of its own: remember the choice now (the replay needs it), the editor applies.
      const r = await bc.call("everest60.save_effect", { effect: id });
      if (!r.ok) toast(t("ui_error", { err: r.error }), "err");
    }
    renderList(); renderHero(); renderControls(); renderKeys();
    if (keep) { const f = fr.col.querySelector(`[data-f="${keep}"]`); if (f) f.focus(); }
  }

  // ── controls of the chosen effect ───────────────────────────────────────
  function renderControls() {
    const s = S.s, e = spec(s.effect);
    if (s.effect === "custom") return;          // the per-key editor's tools live here (renderKeys)
    clear(ctlHost);
    const cm = e.color_modes.includes(s.color_mode) ? s.color_mode : e.color_modes[0];
    const kinds = e.direction ? S.dirs[e.direction] || [] : [];
    const dirId = kinds.some((d) => d.id === s.direction) ? s.direction : (kinds[0] || {}).id;
    s.color_mode = cm || s.color_mode;
    if (dirId) s.direction = dirId;

    // Colour mode and direction as lists, as on the Everest Max: a row of buttons took a whole row
    // (and broke over three lines beside the side ring), the screen needed scrolling.
    const choice = (id, items, value, onChange) => {
      const el = h("select", { class: "field", id }, items.map((o) => h("option", { value: o.value }, o.label)));
      el.value = value;
      el.addEventListener("change", () => onChange(el.value));
      return el;
    };
    const blocks = [];
    if (e.color_modes.length > 1) {
      const sel = choice("fx-mode", e.color_modes.map((m) => ({ value: m, label: t(MODE_KEYS[m]) })), cm, (v) => { s.color_mode = v; renderControls(); });   // renderControls ends in preview()
      blocks.push(field(t("rgb_colormode_label").replace(/:$/, ""), sel, "fx-mode"));
    }
    if (e.has_speed) {
      const r = rangeRow("fx-speed", s.speed, { onInput: (v) => { s.speed = v; preview(); } });
      blocks.push(field(t("rgb_speed_label").replace(/:$/, ""), r.el, "fx-speed"));
    }
    if (e.has_brightness) {
      const r = rangeRow("fx-bri", s.brightness, { onInput: (v) => { s.brightness = v; preview(); } });
      blocks.push(field(t("rgb_brightness_label").replace(/:$/, ""), r.el, "fx-bri"));
    }
    if (e.color_modes.length && cm !== "rainbow") {
      const c1 = colorField("fx-c1", s.color1, { onInput: (c) => { s.color1 = c; preview(); }, label: t("rgb_color1_label") });
      blocks.push(field(t("rgb_color1_label").replace(/:$/, ""), c1.el, "fx-c1"));
    }
    if (e.color_modes.length && cm === "dual") {
      const c2 = colorField("fx-c2", s.color2, { onInput: (c) => { s.color2 = c; preview(); }, label: t("rgb_color2_label") });
      blocks.push(field(t("rgb_color2_label").replace(/:$/, ""), c2.el, "fx-c2"));
    }
    if (kinds.length) {
      const sel = choice("fx-dir", kinds.map((d) => ({ value: d.id, label: d.label })), dirId, (v) => { s.direction = v; preview(); });
      blocks.push(field(t("rgb_direction_label").replace(/:$/, ""), sel, "fx-dir"));
    }

    const apply = h("button", { class: "cta inline", type: "button", onclick: async () => {
      apply.disabled = true;
      const r = await bc.call("everest60.apply_lighting", {
        effect: s.effect, color_mode: s.color_mode, speed: s.speed, brightness: s.brightness,
        color1: s.color1, color2: s.color2, direction: s.direction });
      apply.disabled = false;
      if (!r.ok) toast(t("ui_error", { err: r.error }), "err");     // what the controller said arrives as rgb_applied
    } }, t("rgb_apply"));

    ctlHost.append(h("div", { class: "panel" },
      h("div", { class: "head" }, h("span", { class: "acc" }), h("h3", { class: "t h", style: "margin:0;font-weight:400" }, effectLabel(e))),
      blocks.length ? h("div", { class: "grid2" }, blocks) : h("p", { class: "help", style: "margin:16px 0 4px" }, t("e60_no_controls")),
      h("div", { class: "actions" }, apply)));
    preview();
  }

  /** The effect on the flat keyboard: an approximation (colours, rainbow, two colours), not the animation. */
  function preview() {
    const s = S.s;
    if (!S.kb || !s) return;
    const e = spec(s.effect);
    const cm = e.color_modes.includes(s.color_mode) ? s.color_mode : e.color_modes[0];
    S.kb.setEffect({ effect: s.effect, kind: cm === "rainbow" ? "rainbow" : cm === "dual" ? "dual" : "single",
      c1: s.color1, c2: s.color2, speed: e.has_speed ? s.speed : 50, direction: e.direction ? s.direction : "",
      brightness: e.has_brightness ? s.brightness : 100 });
  }

  // ── side ring ────────────────────────────────────────────────────────────
  function renderRing() {
    clear(ringHost);
    const c = colorField("ring-c", S.ring.color, { onInput: (v) => { S.ring.color = v; }, label: t("side_leds_color_label") });
    const b = rangeRow("ring-bri", S.ring.brightness, { onInput: (v) => { S.ring.brightness = v; } });
    const apply = h("button", { class: "cta inline", type: "button", onclick: async () => {
      apply.disabled = true;
      const r = await bc.call("everest60.set_side_ring", { color: S.ring.color, brightness: S.ring.brightness });
      apply.disabled = false;
      if (!r.ok) toast(t("ui_error", { err: r.error }), "err");
    } }, t("rgb_apply"));          // the panel's head says what it applies; the long label broke over two lines
    apply.setAttribute("aria-label", t("side_leds_apply"));
    ringHost.append(h("div", { class: "panel" },
      h("div", { class: "head" }, h("span", { class: "acc" }), h("h3", { class: "t h", style: "margin:0;font-weight:400" }, t("side_leds_title"))),
      h("div", { class: "grid2" },
        field(t("side_leds_color_label").replace(/:$/, ""), c.el, "ring-c"),
        field(t("rgb_brightness_label").replace(/:$/, ""), b.el, "ring-bri")),
      h("p", { class: "help", title: t("side_leds_hint") }, t("side_leds_hint")),   // shortened in low windows, whole on hover
      h("div", { class: "actions" }, apply)));
  }

  // ── per-key editor (Custom only) ─────────────────────────────────────────
  async function renderKeys() {
    if (S.s.effect !== "custom") { clear(keyHost); S.perkey = null; return; }
    if (S.perkey) return;
    S.perkey = await buildPerKey(scope, keyHost, ctlHost);
  }

  // ── the picture: the flat keyboard, drawn from the layout ───────────────────
  function renderHero() {
    // The picture stays while the effects change (as on the Everest Max: building it again on every
    // click made fast clicks queue up in the window); built again only after Custom, which has none.
    if (S.kb && S.layout && S.s && S.s.effect !== "custom" && heroHost.firstChild && heroHost.isConnected) { preview(); return; }
    clear(heroHost);
    if (S.kb) S.kb.stopEffect();
    S.kb = null;
    if (!S.layout || !S.s || S.s.effect === "custom") return;      // Custom has its editor below
    const stage = h("div", { class: "kbf-stage" });
    S.kb = renderKeyboard(stage, { model: "everest60", layout: S.layout, mode: "rgb" });
    heroHost.append(stage);
    preview();
  }

  // ── start ────────────────────────────────────────────────────────────────
  const [er, lr, rr, ly] = await Promise.all([bc.call("everest60.effects"), bc.call("everest60.lighting"), bc.call("everest60.side_ring"), bc.call("everest60.layout", { lang: currentLang() })]);
  if (!scope.alive) return;
  if (!er.ok) { fr.main.append(h("div", { class: "empty" }, t("ui_error", { err: er.error }))); return; }
  S.effects = er.effects || [];
  S.dirs = er.directions || {};
  S.s = lr.ok ? lr : { effect: "static", color_mode: "dual", speed: 50, brightness: 100, color1: [255, 0, 0], color2: [0, 0, 255], direction: "l2r" };
  if (ly.ok) S.layout = ly;
  if (rr.ok) S.ring = { color: rr.color, brightness: rr.brightness };
  renderList(); renderHero(); renderControls(); renderRing(); renderKeys();
}

export function unmount() { if (scope) scope.dispose(); scope = null; }
