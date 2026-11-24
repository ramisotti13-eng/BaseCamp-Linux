// Everest Max, RGB: the effect list with its controls, and "Individuell" (an effect and colour per key).
import bc from "../bridge.js";
import { t } from "../i18n.js";
import { h, icon, clear, toast, makeScope, confirmDialog } from "../ui.js";
import { colorInput } from "../colorpicker.js";
import { frame, statusBar, cmd, loadKeyboardInfo } from "./em-common.js";
import { renderKeyboard, RING } from "../keyboard-flat.js";
import { PK_FX, PK_VARIANTS, fxSetting, typeOf, paintKeys, resetAll, writeParams, needsSave } from "../individuell.js";

// The effect list, as Base Camp shows it. A family groups the controller's variants (rainbow, two colours).
const FAMILIES = [
  { id: "static", icon: "sun", variants: ["static"] },
  { id: "wave", icon: "wave", variants: ["wave", "wave-rainbow"] },
  { id: "tornado", icon: "tornado", variants: ["tornado", "tornado-rainbow"] },
  { id: "breathing", icon: "breath", variants: ["breathing", "breathing-rainbow", "breathing-dual"] },
  { id: "reactive", icon: "bolt", variants: ["reactive"] },
  { id: "matrix", icon: "matrix", variants: ["matrix"] },
  { id: "custom", icon: "brush", variants: [], custom: true },
  { id: "yeti", icon: "paw", variants: ["yeti"] },
  { id: "off", icon: "bulb-off", variants: ["off"] },
];
const familyOf = (effectId) => FAMILIES.find((f) => f.variants.includes(effectId)) || FAMILIES[0];
const variantKind = (id) => (id.endsWith("-rainbow") ? "rainbow" : id.endsWith("-dual") ? "dual" : "single");

// Individuell: every change goes to the keyboard this long after the last one (Base Camp writes at once too).
const WRITE_MS = 300;
/** Saving into the keyboard's custom slot: when the screen is left, or this long after the last change. */
const SAVE_MS = 10000;

const hex = (c) => "#" + c.map((v) => Math.max(0, Math.min(255, v | 0)).toString(16).padStart(2, "0")).join("");
const rgbOf = (s) => { const v = s.replace("#", ""); return [0, 2, 4].map((i) => parseInt(v.slice(i, i + 2), 16)); };

let scope = null;

export async function mount(el) {
  scope = makeScope();
  const fr = frame(el, "rgb", true);
  const sb = statusBar(scope);
  const tr = sb.tracker;
  const host = h("div", { style: "width:100%;display:flex;flex-direction:column;align-items:center" });
  fr.main.append(sb.el, host);

  const S = {
    effects: new Map(),                    // id -> effect_info
    d: { family: "static", variant: "static", speed: 50, brightness: 100, color1: "#ff0000", color2: "#0000ff", direction: "" },
    editor: null, layout: null, modules: { numpad: true, dock: true }, kb: null,                          // Individuell, built when it is first opened
    touched: false,                        // the user picked an effect before the saved settings arrived
    pics: [null, null, null, null], thumbs: new Map(),     // what D1-D4 show
  };
  // The keyboard picture stays while the effects change: building it again on every click took the
  // window (Qt WebEngine without GPU rendering) long enough that fast clicks queued up and the
  // selection then ran through them. It is built again after Individuell and on new modules.
  let kbHost = null, panelHost = null, pendingRender = 0;
  scope.add(() => { if (pendingRender) cancelAnimationFrame(pendingRender); });

  scope.add(() => { if (S.kb) S.kb.stopEffect(); });                 // no frames after the screen is gone

  /** D1-D4 with the pictures they show, for the keyboard picture. */
  function dkeyList() {
    return S.pics.map((p, i) => {
      if (p && !S.thumbs.has(p)) S.thumbs.set(p, bc.image(p, 128));
      return { idx: i, label: "D" + (i + 1), thumb: p ? S.thumbs.get(p) : null };
    });
  }

  // ── the effect column ───────────────────────────────────────────────────
  function renderList() {
    const col = fr.col;
    const keep = col.contains(document.activeElement) ? document.activeElement.getAttribute("data-f") : null;
    clear(col);
    col.append(h("div", { class: "head" }, h("span", { class: "acc" }), h("h2", { class: "t h", style: "margin:0;font-weight:400" }, t("em_effect_title"))));
    const rows = h("div", { class: "rows" });
    for (const f of FAMILIES) {
      rows.append(h("div", { class: "row" + (f.id === S.d.family ? " on" : "") },
        h("button", { class: "pick", type: "button", "data-f": f.id, "aria-pressed": f.id === S.d.family ? "true" : "false", onclick: () => chooseFamily(f.id) },
          h("div", { class: "tile fx" }, icon(f.icon)),
          h("div", { class: "txt" }, h("div", { class: "nm h" }, t("em_fx_" + f.id))))));
    }
    col.append(rows);
    if (keep) { const b = col.querySelector(`[data-f="${keep}"]`); if (b) b.focus(); }
  }

  /** Marks the chosen effect in the column without building it again. */
  function markList() {
    for (const b of fr.col.querySelectorAll(".pick[data-f]")) {
      const on = b.getAttribute("data-f") === S.d.family;
      b.setAttribute("aria-pressed", on ? "true" : "false");
      if (b.parentElement) b.parentElement.classList.toggle("on", on);
    }
  }

  function chooseFamily(id) {
    const f = FAMILIES.find((x) => x.id === id);
    S.d.family = id;
    S.touched = true;
    if (!f.custom && !f.variants.includes(S.d.variant)) {
      S.d.variant = f.variants[0];
      const info = S.effects.get(S.d.variant);
      if (info && info.directions && info.directions.length && !info.directions.includes(S.d.direction)) S.d.direction = info.directions[0];
    }
    markList();
    // Several clicks before the next frame draw the last one only.
    if (!pendingRender) pendingRender = requestAnimationFrame(() => { pendingRender = 0; if (scope.alive) renderMain(); });
  }

  // ── an effect and its controls ─────────────────────────────────────────────
  function colorField(id, label, key) {
    const inp = colorInput({ id, class: "colorin", value: S.d[key], "aria-label": label });
    const out = h("output", { for: id }, S.d[key]);
    inp.addEventListener("input", () => { S.d[key] = inp.value; out.textContent = inp.value; preview(); });
    return h("div", null, h("label", { class: "lab", for: id }, label), h("div", { class: "range-row" }, inp, out));
  }
  function slider(id, label, key, min, max) {
    const inp = h("input", { type: "range", id, min, max, step: 1, value: S.d[key] });
    const out = h("output", { for: id }, S.d[key] + (key === "brightness" ? " %" : ""));
    inp.addEventListener("input", () => { S.d[key] = Number(inp.value); out.textContent = inp.value + (key === "brightness" ? " %" : ""); preview(); });
    return h("div", null, h("label", { class: "lab", for: id }, label), h("div", { class: "range-row" }, inp, out));
  }

  /** The keyboard picture and the place for the effect's panel; kept while they are on screen. */
  function ensureStage() {
    if (kbHost && kbHost.isConnected && panelHost && panelHost.isConnected) return;
    clear(host);
    if (S.kb) S.kb.stopEffect();
    S.kb = null;
    kbHost = h("div", { class: "kbf-stage" });
    if (S.layout) {
      S.kb = renderKeyboard(kbHost, { model: "everest_max", layout: S.layout, modules: S.modules, mode: "rgb" });
      S.kb.setDKeys(dkeyList());
    }
    panelHost = h("div", { style: "width:100%" });
    host.append(kbHost, panelHost);
  }

  function renderMain() {
    const f = FAMILIES.find((x) => x.id === S.d.family);
    if (f.custom) { kbHost = null; clear(host); renderEditor(); return; }
    if (S.editor) S.editor.flush();
    const info = S.effects.get(S.d.variant) || {};
    const fields = [];
    if (f.variants.length > 1) {
      const sel = h("select", { class: "field", id: "fx-variant" }, f.variants.map((v) => h("option", { value: v }, t("em_var_" + variantKind(v)))));
      sel.value = S.d.variant;
      sel.addEventListener("change", () => {
        S.d.variant = sel.value;
        const i2 = S.effects.get(S.d.variant);
        if (i2 && i2.directions && i2.directions.length && !i2.directions.includes(S.d.direction)) S.d.direction = i2.directions[0];
        renderMain();
      });
      fields.push(h("div", null, h("label", { class: "lab", for: "fx-variant" }, t("em_colour_mode")), sel));
    }
    if (info.color1) fields.push(colorField("fx-c1", t("em_colour1"), "color1"));
    if (info.color2) fields.push(colorField("fx-c2", t("em_colour2"), "color2"));
    if (info.speed) fields.push(slider("fx-speed", t("em_speed"), "speed", 1, 100));
    if (info.brightness) fields.push(slider("fx-bri", t("em_brightness"), "brightness", 0, 100));
    if (info.direction && info.directions && info.directions.length) {
      const sel = h("select", { class: "field", id: "fx-dir" }, info.directions.map((d) => h("option", { value: d }, d)));
      sel.value = info.directions.includes(S.d.direction) ? S.d.direction : info.directions[0];
      S.d.direction = sel.value;
      sel.addEventListener("change", () => { S.d.direction = sel.value; preview(); });
      fields.push(h("div", null, h("label", { class: "lab", for: "fx-dir" }, t("em_direction")), sel));
    }
    const apply = h("button", { class: "cta inline", type: "button", id: "fx-apply", onclick: async () => {
      apply.disabled = true;
      const params = { effect: S.d.variant, speed: S.d.speed, brightness: S.d.brightness, color1: S.d.color1, color2: S.d.color2 };
      if (info.direction) params.direction = S.d.direction;
      const r = await cmd("apply_rgb", params);
      apply.disabled = false;
      if (r.ok) toast(t("em_rgb_sending"), "ok");
    } }, t("em_apply"));
    ensureStage();
    clear(panelHost);
    panelHost.append(h("div", { class: "panel", style: "margin-top:24px" },
      h("div", { class: "head" }, h("span", { class: "acc" }), h("h2", { class: "t h", style: "margin:0;font-weight:400" }, t("em_fx_" + f.id))),
      fields.length ? h("div", { class: "grid2" }, fields) : h("div", { class: "empty", style: "padding-left:0" }, t("em_fx_nothing")),
      h("div", { class: "actions" }, apply)));
    preview();
  }

  function preview() {
    if (!S.kb) return;
    const v = S.d.variant, fam = familyOf(v).id;
    S.kb.setEffect({ effect: fam, kind: variantKind(v), c1: rgbOf(S.d.color1), c2: rgbOf(S.d.color2),
      speed: S.d.speed, direction: S.d.direction, brightness: S.d.brightness });
  }

  // ── Individuell ──────────────────────────────────────────────────────────
  async function renderEditor() {
    if (!S.editor) {
      host.append(h("div", { class: "empty" }, t("em_loading")));
      const pr = await bc.call("everest_max.per_key");
      if (!scope.alive) return;
      if (!S.layout) { clear(host); toast(t("ui_error", { err: "layout" }), "err"); return; }
      if (!S.editor) S.editor = makeEditor(S.layout, pr.ok ? pr : null);
    }
    if (S.d.family !== "custom") return;
    clear(host);
    host.append(S.editor.el);
    S.editor.refresh();
  }

  /** Like Base Camp's Individuell: the panel holds a brush (effect, type, colours, speed, direction).
   *  A click paints a key with it, a frame paints every key in it, Ctrl-click adds a key and paints
   *  it, and changing the brush repaints the keys selected now. Every change goes to the keyboard
   *  WRITE_MS after the last one (the core keeps only the newest waiting write). It is shown, not saved
   *  (one blink); the save into the custom slot follows when the screen is left or SAVE_MS after the
   *  last change, unless a per-key effect is in use: that is saved with every write.
   *  An effect's settings are shared by every key that runs it (one parameter packet per effect), and the ring LEDs can only be static: they are not in the effect table. */
  function makeEditor(L, saved) {
    const nLeds = L.num_leds, nSide = L.num_side_leds;
    const E = {
      leds: Array.from({ length: nLeds }, (_, i) => (saved && saved.leds[i] ? saved.leds[i].slice() : [20, 20, 20])),
      side: Array.from({ length: nSide }, (_, i) => (saved && saved.side[i] ? saved.side[i].slice() : [255, 255, 255])),
      bri: saved ? saved.brightness : 100,
      // the effect of every key (index = LED index) and the settings of each effect, shared by its keys
      fx: Array.from({ length: nLeds }, (_, i) => (saved && saved.effects && PK_FX.includes(saved.effects[i]) ? saved.effects[i] : "static")),
      params: {},
      sel: new Set(),
      brush: "static",
      colour: "#ffffff",                   // the static brush's colour (Base Camp's default Color1)
      displays: null,                      // {synced, percent} as the keyboard has them, null until read
      displaysTouched: false,
    };
    for (const [id, p] of Object.entries((saved && saved.effect_params) || {})) {
      if (!PK_FX.includes(id) || !p) continue;
      E.params[id] = { effect: p.effect || id, speed: p.speed ?? 60, color1: "#" + String(p.color1 || "0044ff").replace("#", ""),
        color2: "#" + String(p.color2 || "0044ff").replace("#", ""), direction: p.direction_label || "" };
    }
    const setting = (id) => fxSetting(E, id);
    const kindOf = (v) => typeOf(v, S.effects.get(v));

    // ── the picture ──
    const stage = h("div", { class: "kbf-stage" });
    let kb = null;
    function buildPicture() {
      if (kb) kb.stopEffect();
      clear(stage);
      kb = renderKeyboard(stage, { model: "everest_max", layout: L, modules: { ...S.modules, dock: false }, mode: "rgb", split: true,
        selection: E.sel, onSelect: (set) => { E.sel = set; if (paintKeys(E, E.sel)) changed(); } });
      kb.setDKeys(dkeyList());
      lockRing();
      draw();
    }
    /** While the brush is not static the ring LEDs cannot be painted. */
    function lockRing() {
      kb.setDisabled(E.brush === "static" ? [] : Array.from({ length: nSide }, (_, i) => RING + i));
      E.sel = kb.getSelection();
    }
    const still = (c) => ({ effect: "static", kind: "single", c1: c, brightness: E.bri });
    function fxSpec(id) {
      if (id === "off") return { effect: "off" };
      const p = setting(id);
      return { effect: id, kind: kindOf(p.effect), c1: rgbOf(p.color1), c2: rgbOf(p.color2), speed: p.speed, direction: p.direction, brightness: E.bri };
    }
    /** The picture as the keyboard runs it: every key its effect (animated), the ring its colours. */
    function draw() {
      const shared = new Map(), map = new Map();
      for (const id of kb.ids()) {
        if (id >= RING) { map.set(id, still(E.side[id - RING])); continue; }
        const f = E.fx[id];
        if (f === "static") { map.set(id, still(E.leds[id])); continue; }
        if (!shared.has(f)) shared.set(f, fxSpec(f));
        map.set(id, shared.get(f));
      }
      kb.setKeyEffects(map);
    }

    // ── painting and writing ──
    function changed() { draw(); queueWrite(); }
    /** The brush's settings changed: the selected keys take them, and so does every key that runs
     *  the effect already (its settings are shared). */
    function brushChanged() {
      const painted = paintKeys(E, E.sel);
      if (painted || (E.brush !== "static" && E.fx.includes(E.brush))) changed();
    }
    let writeTimer = null, saveTimer = null;
    let unsaved = false;                 // changes the keyboard's custom slot does not have yet
    function queueWrite() {
      unsaved = true;
      if (writeTimer != null) scope.cancel(writeTimer);
      writeTimer = scope.later(write, WRITE_MS);
      if (saveTimer != null) scope.cancel(saveTimer);
      saveTimer = scope.later(save, SAVE_MS);
    }
    function write() {
      writeTimer = null;
      if (needsSave(E)) { save(); return; }
      cmd("apply_per_key", writeParams(E, false));
    }
    /** One write that also saves the picture (and drops the waiting timers: nothing is written twice). */
    function save() {
      if (writeTimer != null) { scope.cancel(writeTimer); writeTimer = null; }
      if (saveTimer != null) { scope.cancel(saveTimer); saveTimer = null; }
      if (!unsaved) return;
      unsaved = false;
      cmd("apply_per_key", writeParams(E, true));
    }
    // A change made just before the screen goes still reaches the keyboard, and is saved.
    scope.add(() => { if (unsaved) save(); });

    // ── the panel: Effekt auswählen, Typ, Geschwindigkeit, Richtung ──
    const fxSel = h("select", { class: "field", id: "ind-fx" }, PK_FX.map((id) => h("option", { value: id }, t("em_fx_" + id))));
    fxSel.value = E.brush;
    fxSel.addEventListener("change", () => {
      E.brush = fxSel.value;
      if (E.brush !== "static" && E.brush !== "off") setting(E.brush);
      lockRing();
      renderBrush();
      if (paintKeys(E, E.sel)) changed();
    });
    const motion = h("div", { class: "indcol" });           // Richtung and Geschwindigkeit of the brush's effect
    const types = h("div", { class: "indcol" });
    /** A type of the brush's effect: its name and the colours it uses. */
    function typeRow(v, p) {
      const info = S.effects.get(v) || {};
      const kind = kindOf(v), rid = "ind-type-" + v;
      const radio = h("input", { type: "radio", name: "ind-type", id: rid, class: "sr-only", value: v });
      radio.checked = p ? p.effect === v : true;
      const row = h("div", { class: "trow" + (radio.checked ? " on" : ""), "data-v": v }, radio, h("label", { for: rid }, t("em_ind_" + kind)));
      const pick = () => {
        if (!p || p.effect === v) return;
        p.effect = v;
        for (const r of types.querySelectorAll(".trow")) { const on = r.dataset.v === v; r.classList.toggle("on", on); r.querySelector("input").checked = on; }
        brushChanged();
      };
      radio.addEventListener("change", pick);
      const keys = E.brush === "static" ? ["colour"] : ["color1", "color2"].filter((k) => info[k]);
      keys.forEach((k, n) => {
        const cur = k === "colour" ? E.colour : p[k];
        const sw = colorInput({ class: "tsw", id: "ind-" + k + "-" + v, value: cur, "aria-label": t("em_ind_swatch", { type: t("em_ind_" + kind), n: n + 1 }) });
        sw.addEventListener("click", pick);
        sw.addEventListener("input", () => {
          if (k === "colour") E.colour = sw.value; else p[k] = sw.value;
          brushChanged();
        });
        row.append(sw);
      });
      return row;
    }
    function renderBrush() {
      clear(motion); clear(types);
      if (E.brush === "off") return;
      const p = E.brush === "static" ? null : setting(E.brush);
      const variants = PK_VARIANTS[E.brush];
      if (p && !variants.includes(p.effect)) p.effect = variants[0];
      types.append(h("span", { class: "lab" }, t("em_ind_type")), h("div", { class: "ttypes", role: "radiogroup", "aria-label": t("em_ind_type") },
        variants.map((v) => typeRow(v, p))));
      if (!p) return;
      const info = S.effects.get(p.effect) || {};
      if (info.direction && info.directions && info.directions.length) {
        const dir = h("select", { class: "field", id: "ind-dir" }, info.directions.map((d) => h("option", { value: d }, d)));
        if (!info.directions.includes(p.direction)) p.direction = info.directions[0];
        dir.value = p.direction;
        dir.addEventListener("change", () => { p.direction = dir.value; brushChanged(); });
        motion.append(h("div", null, h("label", { class: "lab", for: "ind-dir" }, t("em_direction")), dir));
      }
      if (info.speed) {
        const sp = h("input", { type: "range", id: "ind-speed", min: 1, max: 100, step: 1, value: p.speed });
        sp.addEventListener("input", () => { p.speed = Number(sp.value); brushChanged(); });
        motion.append(h("div", null, h("label", { class: "lab", for: "ind-speed" }, t("em_speed")), h("div", { class: "range-row" }, sp)));
      }
    }

    // ── Helligkeit: the displays (when not synchronised) and the backlight ──
    const sunRow = (inp) => h("div", { class: "sunrow" }, icon("sun"), inp, h("span", { class: "big" }, icon("sun")));
    const bri = h("input", { type: "range", id: "ind-bri", min: 10, max: 100, step: 1, value: E.bri });
    bri.addEventListener("input", () => { E.bri = Number(bri.value); changed(); });
    // The D-key displays take only 25 % steps (0, 25, 50, 75, 100), as Base Camp's slider.
    const disp = h("input", { type: "range", id: "ind-disp", min: 0, max: 100, step: 25, value: 100 });
    const dispBox = h("div", { hidden: true }, h("label", { class: "lab sub", for: "ind-disp" }, t("em_ind_displays")), sunRow(disp));
    const sync = h("input", { type: "checkbox", id: "ind-sync", disabled: true });
    const syncBox = h("label", { class: "check indsync", for: "ind-sync", hidden: true }, sync, t("em_ind_sync"));
    const hasDisplays = () => S.modules.numpad || S.modules.dock;
    function paintDisplays() {
      const d = E.displays;
      syncBox.hidden = !hasDisplays();
      sync.disabled = !d;
      sync.checked = !d || d.synced;
      dispBox.hidden = !hasDisplays() || !d || d.synced;
      if (d && !d.synced && document.activeElement !== disp) disp.value = d.percent;
    }
    let dispTimer = null;
    function writeDisplays() {
      if (dispTimer != null) { scope.cancel(dispTimer); dispTimer = null; }
      const d = E.displays;
      cmd("set_dock_settings", { displays: d.synced ? { synced: true } : { synced: false, percent: d.percent } });
    }
    sync.addEventListener("change", () => {
      E.displaysTouched = true;
      // Unticked, the displays keep the brightness they had: the backlight's.
      E.displays = sync.checked ? { synced: true, percent: null } : { synced: false, percent: E.displays.percent ?? Math.round(E.bri / 25) * 25 };
      paintDisplays();
      writeDisplays();
    });
    disp.addEventListener("input", () => {
      E.displaysTouched = true;
      E.displays = { synced: false, percent: Number(disp.value) };
      if (dispTimer != null) scope.cancel(dispTimer);
      dispTimer = scope.later(writeDisplays, WRITE_MS);
    });
    scope.on("everest_max.dock_settings", (_t, p) => {
      if (E.displaysTouched || !p || !p.settings || !p.settings.displays) return;
      E.displays = p.settings.displays;
      paintDisplays();
    });
    scope.add(() => { if (dispTimer != null) writeDisplays(); });
    /** What the keyboard has: the last read when there is one, else a read (its answer comes as an event). */
    async function readDisplays() {
      if (!hasDisplays() || E.displays) return;
      const r = await bc.call("everest_max.dock_settings", { refresh: false });
      if (!scope.alive || !r.ok || E.displaysTouched) return;
      if (r.settings && r.settings.displays) { E.displays = r.settings.displays; paintDisplays(); }
      else bc.call("everest_max.dock_settings");
    }

    // ── Reset: everything static white, as Base Camp's defaults ──
    const resetBtn = h("button", { class: "btn indreset", type: "button", id: "ind-reset", onclick: async () => {
      if (!(await confirmDialog({ title: t("em_ind_reset_title"), message: t("em_ind_reset_msg"), okLabel: t("em_ind_reset"), danger: true }))) return;
      resetAll(E);
      changed();
    } }, icon("refresh"), t("em_ind_reset"));

    const tip = t("em_pk_fx_tip");
    const panel = h("section", { class: "panel indpanel" },
      h("div", { class: "head" }, h("span", { class: "acc" }), h("h2", { class: "t h", style: "margin:0;font-weight:400;flex:none" }, t("em_fx_custom")),
        h("span", { class: "qhelp", tabindex: "0", role: "note", "aria-label": tip, "data-tip": tip }, "?"),
        syncBox, resetBtn),
      h("div", { class: "indgrid" },
        h("div", { class: "indcol" }, h("div", null, h("label", { class: "lab", for: "ind-fx" }, t("em_effect_title")), fxSel), motion),
        types,
        h("div", { class: "indcol" }, h("span", { class: "lab" }, t("em_brightness")), dispBox,
          h("div", null, h("label", { class: "lab sub", for: "ind-bri" }, t("em_ind_backlight")), sunRow(bri)))));

    const el = h("div", { class: "editor indiv" }, stage, panel);
    buildPicture();
    renderBrush();
    paintDisplays();
    return {
      el,
      refresh() { draw(); paintDisplays(); readDisplays(); },
      /** Leaving Individuell for an effect: save now, so the waiting save cannot put the keyboard
       *  back into custom mode after the effect was applied. */
      flush() { if (unsaved) save(); },
      /** Other modules plugged in: the picture again, the brush and the keys as they are. */
      setModules() { buildPicture(); paintDisplays(); readDisplays(); },
    };
  }

  // ── start ────────────────────────────────────────────────────────────────
  // Individuell writes at once, so only a failure is worth a message there.
  tr.onDone((p) => {
    if (!["rgb", "per_key"].includes(p.kind)) return;
    if (p.ok === false) toast(t("em_rgb_failed", { err: p.error || "" }), "err");
    else if (p.kind === "rgb") toast(t("em_rgb_done"), "ok");
  });
  renderList();
  const [er, sr, ki, pics] = await Promise.all([bc.call("everest_max.effects"), bc.call("everest_max.rgb_settings"), loadKeyboardInfo(), bc.call("everest_max.pictures")]);
  if (!scope.alive) return;
  S.layout = ki.layout; S.modules = ki.modules;
  if (pics.ok && Array.isArray(pics.dbuttons)) S.pics = pics.dbuttons;
  if (er.ok) for (const e of er.effects) S.effects.set(e.id, e);
  if (sr.ok) {
    const f = familyOf(sr.effect);
    const saved = { family: f.id, variant: f.variants.includes(sr.effect) ? sr.effect : f.variants[0] };
    // An effect clicked while these were on their way stays chosen.
    S.d = { ...S.d, ...(S.touched ? {} : saved),
      speed: sr.speed, brightness: sr.brightness, color1: hex(sr.color1), color2: hex(sr.color2), direction: sr.direction_label || "" };
  }
  renderList();
  renderMain();
  scope.on("everest_max.modules", (_t, p) => {
    S.modules = { numpad: !!(p && p.numpad), dock: !!(p && p.dock), known: true };
    if (S.editor) S.editor.setModules();
    if (S.d.family !== "custom") { kbHost = null; renderMain(); }   // a new picture
  });
}

export function unmount() { if (scope) scope.dispose(); scope = null; }
