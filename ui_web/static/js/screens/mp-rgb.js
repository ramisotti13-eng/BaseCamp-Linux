// MacroPad, RGB: the effect list, the controls the chosen effect reads, per-key colours for Custom,
// and "save to pad".
import bc from "../bridge.js";
import { t } from "../i18n.js";
import * as devices from "../devices.js";
import { h, icon, clear, toast, makeScope } from "../ui.js";
import { colorField, rangeRow, segmented, field, toHex } from "../rgbkit.js";
import { frame, statusBar, padPhoto, drawPad, errText } from "./mp-common.js";

let scope = null;

const DIRECTION_KEYS = { wave: ["mp_dir_right", "mp_dir_left", "mp_dir_down", "mp_dir_up"], tornado: ["mp_dir_cw", "mp_dir_ccw"] };

export async function mount(el) {
  scope = makeScope();
  const fr = frame(el, "rgb", true);
  const S = { L: null, effects: [], controls: {}, directions: {}, sel: 0, photo: null, pressed: null, quiet: 0 };
  const sb = statusBar(scope);
  const padHost = h("div", { style: "width:100%;display:flex;flex-direction:column;align-items:center" });
  const ctlHost = h("div", { style: "width:100%" });
  fr.main.append(sb.el, padHost, ctlHost);

  const effectKey = (id) => (S.effects.find((e) => e.id === id) || {}).key || String(id);
  const effectLabel = (id) => { const k = "mp_effect_" + effectKey(id); const s = t(k); return s === k ? effectKey(id) : s; };

  async function load() {
    const r = await bc.call("macropad.lighting");
    if (!scope.alive) return false;
    if (!r.ok) { toast(t("ui_error", { err: r.error }), "err"); return false; }
    S.L = r.lighting; S.effects = r.effects || []; S.controls = r.controls || {}; S.directions = r.directions || {};
    return true;
  }

  /** Change lighting fields; the core saves them and answers with the whole lighting. */
  async function update(fields) {
    S.quiet = Date.now() + 500;            // our own change comes back as an event; no need to redraw for it
    const r = await bc.call("macropad.update_lighting", fields);
    if (!r.ok) { toast(t("ui_error", { err: r.error }), "err"); return false; }
    if (r.lighting) S.L = r.lighting;
    return true;
  }

  // ── the effect list ─────────────────────────────────────────────────────
  function renderList() {
    const col = fr.col;
    clear(col);
    col.append(h("div", { class: "head" }, h("span", { class: "acc" }), h("h2", { class: "t h", style: "margin:0;font-weight:400" }, t("e60_effects_title"))));
    const rows = h("div", { class: "rows" });
    for (const e of S.effects) {
      const on = S.L.effect === e.id;
      rows.append(h("div", { class: "row" + (on ? " on" : "") },
        h("button", { class: "pick", type: "button", "aria-pressed": on ? "true" : "false", "data-f": "fx-" + e.id, onclick: () => choose(e.id) },
          h("div", { class: "tile rgbfx" }, icon(e.key === "custom" ? "keys" : "shape")),
          h("div", { class: "txt" }, h("div", { class: "nm h" }, effectLabel(e.id))))));
    }
    col.append(rows);
  }

  async function choose(id) {
    const keep = document.activeElement && document.activeElement.getAttribute("data-f");
    if (!(await update({ effect: id }))) return;
    renderAll();
    if (keep) { const f = fr.col.querySelector(`[data-f="${keep}"]`); if (f) f.focus(); }
  }

  // ── controls ──────────────────────────────────────────────────────────────
  const sendResult = (r) => { if (!r.ok) toast(errText(r.error), "err"); };    // the pad's answer arrives as macropad.status

  function renderControls() {
    clear(ctlHost);
    const L = S.L, id = L.effect, key = effectKey(id);
    const [hasSpeed, hasC1, hasC2, hasDir] = S.controls[String(id)] || [true, true, true, false];
    const blocks = [];
    if (key !== "off") {
      const b = rangeRow("fx-bri", L.brightness, { onChange: (v) => update({ brightness: v }) });
      blocks.push(field(t("rgb_brightness_label").replace(/:$/, ""), b.el, "fx-bri"));
    }
    if (hasSpeed) {
      const r = rangeRow("fx-speed", L.speed, { onChange: (v) => update({ speed: v }) });
      blocks.push(field(t("rgb_speed_label").replace(/:$/, ""), r.el, "fx-speed"));
    }
    if (hasC1) {
      const c = colorField("fx-c1", L.color1, { onChange: (v) => update({ color1: v }), label: t("rgb_color1_label") });
      blocks.push(field(t("rgb_color1_label").replace(/:$/, ""), c.el, "fx-c1"));
    }
    if (hasC2) {
      const c = colorField("fx-c2", L.color2, { onChange: (v) => update({ color2: v }), label: t("rgb_color2_label") });
      blocks.push(field(t("rgb_color2_label").replace(/:$/, ""), c.el, "fx-c2"));
    }
    const n = S.directions[String(id)] || 0;
    if (hasDir && n) {
      const keys = (DIRECTION_KEYS[key] || []).slice(0, n);
      const seg = segmented(t("rgb_direction_label"), keys.map((k, i) => ({ value: i, label: t(k) })), L.direction, (v) => update({ direction: v }));
      blocks.push(field(t("rgb_direction_label").replace(/:$/, ""), seg.el, null, "wide"));
    }

    let custom = null;
    if (key === "custom") {
      const i = S.sel, c = L.colors[i] || [255, 255, 255];
      const cf = colorField("key-c", c, {
        label: t("custom_rgb_key_color"),
        onChange: async (v) => {
          S.quiet = Date.now() + 500;
          const r = await bc.call("macropad.set_key_color", { index: i, color: v });
          if (!r.ok) return toast(t("ui_error", { err: r.error }), "err");
          L.colors[i] = v; renderPad();
        } });
      custom = h("div", { class: "wide" },
        h("span", { class: "lab" }, t("mp_key_color_of", { n: i + 1 })), cf.el,
        h("p", { class: "help" }, t("mp_custom_hint")));
    }

    const apply = h("button", { class: "cta inline", type: "button", onclick: async () => {
      apply.disabled = true;
      sendResult(await bc.call(key === "custom" ? "macropad.apply_key_colors" : "macropad.apply_lighting"));
      apply.disabled = false;
      if (key === "custom") { await load(); renderList(); }      // applying the colours makes Custom the effect
    } }, key === "custom" ? t("mp_apply_colors") : t("rgb_apply"));
    const save = h("button", { class: "btn", type: "button", onclick: async () => {
      save.disabled = true;
      sendResult(await bc.call("macropad.save_to_device"));
      save.disabled = false;
    } }, t("mp_save_device"));

    ctlHost.append(h("div", { class: "panel", style: "margin-top:18px" },
      h("div", { class: "head" }, h("span", { class: "acc" }), h("h3", { class: "t h", style: "margin:0;font-weight:400" }, effectLabel(id))),
      blocks.length || custom ? h("div", { class: "grid2" }, blocks, custom) : h("p", { class: "help", style: "margin:16px 0 4px" }, t("e60_no_controls")),
      h("div", { class: "actions" }, apply, save),
      h("p", { class: "help" }, t("mp_save_help"))));
  }

  // ── the pad: shown for Custom, where each key has a colour of its own ────
  function renderPad() {
    if (effectKey(S.L.effect) !== "custom") { clear(padHost); return; }
    drawPad(padHost, {
      photo: S.photo, sel: S.sel, pressed: S.pressed, label: t("mp_grid_label"),
      key: (i) => { const c = S.L.colors[i] || [255, 255, 255];
        return { assigned: false, text: "", color: c, aria: "M" + (i + 1) + ", " + toHex(c) }; },
      onSelect: (i) => { S.sel = i; renderPad(); renderControls(); } });
  }

  async function updatePhoto() {
    const next = await padPhoto();
    if (!scope.alive) return;
    if ((next && next.url) !== (S.photo && S.photo.url)) { S.photo = next; if (S.L) renderPad(); }
  }
  scope.add(devices.onPhotos(updatePhoto));

  function renderAll() { renderList(); renderPad(); renderControls(); }

  // ── events ───────────────────────────────────────────────────────────────
  scope.on("macropad.lighting_changed", async () => { if (Date.now() < S.quiet) return; if (await load()) renderAll(); });
  scope.on("macropad.key", (_t, p) => {
    if (!p || typeof p.index !== "number" || effectKey(S.L.effect) !== "custom") return;
    S.pressed = p.index; renderPad();
    scope.later(() => { if (S.pressed === p.index) { S.pressed = null; renderPad(); } }, 350);
  });

  // ── start ────────────────────────────────────────────────────────────────
  if (!(await load())) return;
  renderAll();
  await updatePhoto();
}

export function unmount() { if (scope) scope.dispose(); scope = null; }
