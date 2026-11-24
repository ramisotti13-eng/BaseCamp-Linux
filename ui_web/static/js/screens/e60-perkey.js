// Everest 60, per-key editor: the keyboard drawn from everest60.layout (64 keys and the 44 side-ring
// LEDs), each filled with its colour; select keys, paint them, apply to the board.
import bc from "../bridge.js";
import { t } from "../i18n.js";
import { h, clear, toast } from "../ui.js";
import { colorField, rangeRow } from "../rgbkit.js";
import { renderKeyboard, RING } from "../keyboard-flat.js";
import { currentLang } from "../i18n.js";

const pad = (list, n, fill) => Array.from({ length: n }, (_, i) => (Array.isArray(list[i]) ? list[i].slice(0, 3).map(Number) : fill.slice()));
const DARK = [20, 20, 20];

/** Builds the editor: the keyboard into `host`, the tools (colour, selection, brightness, Apply) into
 *  `toolHost` (beside the side ring, so the screen fits without scrolling). `scope` ends its listeners.
 *  Returns {reload}. */
export async function buildPerKey(scope, host, toolHost) {
  const [lr, pr] = await Promise.all([bc.call("everest60.layout", { lang: currentLang() }), bc.call("everest60.per_key")]);
  if (!scope.alive) return null;
  if (!lr.ok) { host.append(h("div", { class: "empty" }, t("ui_error", { err: lr.error }))); return null; }
  const lay = lr;
  const NK = lay.num_keys || lay.keys.length, NS_ = lay.side.length;
  const S = {
    leds: pad((pr.ok && pr.leds) || [], NK, DARK), side: pad((pr.ok && pr.side) || [], NS_, DARK),
    bri: pr.ok && pr.brightness != null ? pr.brightness : 100,
    sel: new Set(), undo: [], cur: [253, 157, 2], anchor: null,
  };
  const colorOf = (id) => (id < RING ? S.leds[id] : S.side[id - RING]);
  const setColor = (id, c) => { if (id < RING) S.leds[id] = c.slice(); else S.side[id - RING] = c.slice(); };
  const sel = h("span", { class: "chip", style: "background:none;border:0;padding:0" });
  const stage = h("div", { class: "kbf-stage" });
  const kb = renderKeyboard(stage, { model: "everest60", layout: lay, mode: "rgb",
    colors: { leds: S.leds, side: S.side },
    onSelect: (set) => { S.sel = set; count(); } });
  const paintAll = () => { kb.setColors({ leds: S.leds, side: S.side }); count(); };
  const count = () => { sel.textContent = t("custom_rgb_selected", { n: S.sel.size }); };
  const select = (ids) => { S.sel = new Set(ids); kb.setSelection(S.sel); count(); };

  const push = () => { S.undo.push({ leds: S.leds.map((c) => c.slice()), side: S.side.map((c) => c.slice()) }); if (S.undo.length > 30) S.undo.shift(); };

  function fill(c, ids) {
    const list = ids || [...S.sel];
    if (!list.length) return;
    push();
    list.forEach((id) => setColor(id, c));
    paintAll();
  }
  const picker = colorField("pk-color", S.cur, {
    label: t("custom_rgb_key_color"),
    onInput: (c) => { S.cur = c; },
    onChange: (c) => { S.cur = c; fill(c); },
  });
  const brightness = rangeRow("pk-bri", S.bri, { onInput: (v) => { S.bri = v; } });
  const allIds = () => kb.ids();

  const btn = (label, fn, cls) => h("button", { class: "btn" + (cls ? " " + cls : ""), type: "button", onclick: fn }, label);
  const undoBtn = btn(t("custom_rgb_undo"), () => {
    const u = S.undo.pop();
    if (!u) return;
    S.leds = u.leds; S.side = u.side; paintAll();
  });
  const apply = h("button", { class: "cta inline", type: "button", onclick: async () => {
    apply.disabled = true;
    const r = await bc.call("everest60.apply_per_key", { leds: S.leds, side: S.side, brightness: S.bri });
    apply.disabled = false;
    if (!r.ok) toast(t("ui_error", { err: r.error }), "err");     // the result of the send arrives as rgb_applied
  } }, t("custom_rgb_apply"));

  clear(host);
  clear(toolHost);
  host.append(stage);
  toolHost.append(h("div", { class: "panel pkpanel" },
    h("div", { class: "head" }, h("span", { class: "acc" }), h("h3", { class: "t h", style: "margin:0;font-weight:400" }, t("custom_rgb_title")), sel),
    h("div", { class: "pktools" },
      h("div", null, h("span", { class: "lab" }, t("custom_rgb_key_color")), picker.el),
      btn(t("custom_rgb_fill"), () => fill(S.cur)),
      btn(t("custom_rgb_select_all"), () => { select(lay.keys.filter((k) => k.led != null).map((k) => k.led)); }),
      btn(t("e60_select_ring"), () => { select(lay.side.map((_, i) => RING + i)); }),
      btn(t("custom_rgb_deselect"), () => { select([]); }),
      btn(t("custom_rgb_all_black"), () => fill([0, 0, 0], allIds())),
      btn(t("custom_rgb_all_white"), () => fill([255, 255, 255], allIds())),
      undoBtn),
    h("div", { class: "grid2 pkbri" },
      h("div", null, h("label", { class: "lab", for: "pk-bri" }, t("custom_rgb_brightness_short")), brightness.el),
      h("div", { class: "help clamp", style: "align-self:end", title: t("e60_perkey_hint") }, t("e60_perkey_hint"))),
    h("div", { class: "actions" }, apply)));
  paintAll();
  return { reload: async () => {
    const r = await bc.call("everest60.per_key");
    if (!scope.alive || !r.ok) return;
    S.leds = pad(r.leds || [], NK, DARK); S.side = pad(r.side || [], NS_, DARK); S.bri = r.brightness ?? S.bri;
    brightness.set(S.bri); paintAll();
  } };
}
