// Makalu, RGB, laid out like Base Camp's page: the effect list on the left, the mouse picture centred above the panel
// of the chosen effect (type rows with their colour swatches, sliders for speed and brightness, the direction),
// and the light ring of the picture follows every change. Nothing is applied by a button: every change goes to the
// mouse at once, 300 ms after the last one (the newest wins; a busy mouse is asked again). "Individuell" (custom)
// shows eight colour squares around the mouse and an inline colour picker.
import bc from "../bridge.js";
import { t } from "../i18n.js";
import * as devices from "../devices.js";
import { colorInput, colorPanel } from "../colorpicker.js";
import { h, icon, clear, toast, makeScope } from "../ui.js";
import { frame, loadModel, statusBar, watchResults, mouseBox } from "./mk-common.js";

let scope = null;
let flushPending = null;   // sends a change that is still waiting for its 300 ms when the screen closes

const NUM_LEDS = 8;
// Mountain's order of the list. rgb_breathing is not an entry of its own there: it is Breathing's type "Rainbow".
const ORDER = ["static", "rainbow", "breathing", "responsive", "custom", "yeti", "off"];
const ICON = { static: "sun", rainbow: "wave", breathing: "breath", responsive: "bolt", custom: "brush", yeti: "paw", off: "bulb-off" };
// What each effect offers: its types (n = colours the type uses) and the sliders and buttons.
const TYPES = {
  static: [{ id: "single", n: 1 }],
  rainbow: [{ id: "rainbow", n: 0 }],
  breathing: [{ id: "single", n: 1 }, { id: "dual", n: 2 }, { id: "rainbow", n: 0 }],
  responsive: [{ id: "single", n: 1 }],
  yeti: [{ id: "dual", n: 2 }],
};
const HAS = { speed: ["rainbow", "breathing"], direction: ["rainbow"] };
// Base Camp's example colours by square (left top to bottom, then right top to bottom) and the LED each square is.
// The core numbers its LEDs 0 top left down to 3 bottom left, then 4 bottom right up to 7 top right.
const LED_OF_SLOT = [0, 1, 2, 3, 7, 6, 5, 4];
const DEFAULT_SLOT_COLORS = [[255, 0, 0], [0, 255, 0], [0, 0, 255], [255, 0, 255], [255, 255, 0], [0, 255, 255], [255, 255, 255], [0, 0, 0]];
const defaultLeds = () => { const l = []; LED_OF_SLOT.forEach((led, s) => { l[led] = [...DEFAULT_SLOT_COLORS[s]]; }); return l; };
const DEBOUNCE_MS = 300;

const hex = (c) => "#" + c.map((v) => Math.max(0, Math.min(255, v | 0)).toString(16).padStart(2, "0")).join("");
const rgb = (s) => [1, 3, 5].map((i) => parseInt(s.slice(i, i + 2), 16));
const isBlack = (c) => !c || (c[0] | c[1] | c[2]) === 0;

export async function mount(el) {
  scope = makeScope();
  const info = await loadModel();
  if (!scope.alive) return;
  const mine = scope;
  const fr = frame(el, "lighting", true, info.model);
  const sb = statusBar(scope, info);
  watchResults(scope, { quiet: true });   // changes apply all the time: only failures are worth a toast

  const [fxr, lr, cr] = await Promise.all([bc.call("makalu.effects"), bc.call("makalu.lighting"), bc.call("makalu.custom_leds")]);
  if (!scope.alive) return;
  const core = fxr.ok ? fxr.effects || [] : [];
  const have = new Set(core.map((e) => e.id));
  const effects = ORDER.filter((id) => id === "custom" || !core.length || have.has(id));
  const speeds = fxr.ok && fxr.speeds && fxr.speeds.length ? fxr.speeds : [{ id: "slow" }, { id: "medium" }, { id: "fast" }];
  const dirs = fxr.ok && fxr.directions && fxr.directions.length ? fxr.directions : [{ id: "left" }, { id: "right" }];
  const levels = fxr.ok && fxr.brightness_levels && fxr.brightness_levels.length > 1 ? fxr.brightness_levels : [0, 25, 50, 75, 100];
  const bStep = Math.max(1, levels[1] - levels[0]);

  const L = lr.ok ? lr : { effect: "static", speed: "medium", brightness: 100, direction: "right", color1: [0, 118, 204], color2: [255, 0, 0] };
  const S = {
    sel: L.effect === "rgb_breathing" ? "breathing" : effects.includes(L.effect) ? L.effect : "static",
    typ: { breathing: L.effect === "rgb_breathing" ? "rainbow" : isBlack(L.color2) ? "single" : "dual" },
    d: { speed: L.speed, brightness: L.brightness, direction: L.direction, color1: [...L.color1], color2: isBlack(L.color2) ? [255, 0, 0] : [...L.color2] },
    leds: cr.ok && Array.isArray(cr.leds) && cr.leds.length === NUM_LEDS ? cr.leds.map((c) => [...c]) : defaultLeds(),
    ledBri: cr.ok && cr.brightness != null ? cr.brightness : 100,
    picked: new Set([0]),       // the selected squares, as LED numbers
  };
  const typOf = (id) => (S.typ[id] || (TYPES[id] && TYPES[id][0].id));

  const fxName = (id) => t("mk_fx_" + id);
  const speedName = (s) => { const k = "mk_speed_" + s.id; const v = t(k); return v === k ? s.label || s.id : v; };

  // ── what the mouse and the picture get ──────────────────────────────────
  /** The state in the core's terms: effect, speed, direction, brightness, colours (custom: leds). */
  function lightState() {
    const d = S.d;
    if (S.sel === "custom") return { effect: "custom", leds: S.leds, brightness: S.ledBri };
    const base = { effect: S.sel, speed: d.speed, direction: d.direction, brightness: d.brightness, color1: d.color1, color2: d.color2 };
    if (S.sel === "breathing") {
      const ty = typOf("breathing");
      if (ty === "rainbow") base.effect = "rgb_breathing";
      else if (ty === "single") base.color2 = [0, 0, 0];     // one colour: it fades to black and back
    }
    return base;
  }
  let ledLayer = null, squares = [], dragging = false;      // the squares of "Individuell" (buildLeds)
  const mbox = mouseBox(info, { size: "small mkl", decorate: (ring) => buildLeds(ring) });
  const preview = () => mbox.setLight(lightState());

  let timer = null, pending = false;
  async function send() {
    timer = null;
    if (!pending) return;
    pending = false;
    const s = lightState();
    const r = s.effect === "custom"
      ? await bc.call("makalu.set_custom_leds", { leds: s.leds, brightness: s.brightness, preset: "" })
      : await bc.call("makalu.set_lighting", { effect: s.effect, speed: s.speed, brightness: s.brightness, direction: s.direction, color1: s.color1, color2: s.color2 });
    if (!r.ok) toast(t("ui_error", { err: r.error }), "err");
    else if (r.sent === false && r.busy) {                    // still busy with the last one: ask again with the newest state
      pending = true;
      if (!timer && mine.alive) timer = setTimeout(send, 250);
    }
  }
  /** Every change: the picture at once, the mouse after 300 ms of quiet. */
  function changed() {
    preview();
    pending = true;
    clearTimeout(timer);
    timer = setTimeout(send, DEBOUNCE_MS);
  }
  flushPending = () => { if (pending) { clearTimeout(timer); send(); } };
  scope.add(() => { clearTimeout(timer); if (pending) send(); });

  // ── the effect list ─────────────────────────────────────────────────────
  function renderList() {
    const col = fr.col;
    const keep = col.contains(document.activeElement) ? document.activeElement.getAttribute("data-f") : null;
    clear(col);
    col.append(h("div", { class: "head" }, h("span", { class: "acc" }), h("h2", { class: "t h", style: "margin:0;font-weight:400" }, t("mk_effect_pick"))));
    const rows = h("div", { class: "rows" });
    for (const id of effects) {
      rows.append(h("div", { class: "row" + (S.sel === id ? " on" : "") },
        h("button", { class: "pick", type: "button", "data-f": "fx" + id, "aria-pressed": S.sel === id ? "true" : "false", onclick: () => select(id) },
          h("div", { class: "tile fxtile", "aria-hidden": "true" }, icon(ICON[id])),
          h("div", { class: "txt" }, h("div", { class: "nm h" }, fxName(id))))));
    }
    col.append(rows);
    if (keep) { const f = col.querySelector(`[data-f="${keep}"]`); if (f) f.focus(); }
  }
  function select(id) {
    if (S.sel === id) return;
    S.sel = id;
    renderAll();
    changed();
  }

  // ── the panel ───────────────────────────────────────────────────────────
  const detail = h("div", { class: "panel mkpanel mklpanel" });
  let inlinePicker = null;
  const dropPicker = () => { if (inlinePicker) { inlinePicker.destroy(); inlinePicker = null; } };
  scope.add(dropPicker);

  const sliderRow = (id, min, max, step, value, lo, hi, onInput, label) => {
    const r = h("input", { type: "range", id, min, max, step, value, "aria-label": label });
    r.addEventListener("input", () => onInput(Number(r.value)));
    return h("div", { class: "range-row mklslide" }, h("span", { class: "rlo", "aria-hidden": "true" }, lo), r, h("span", { class: "rhi", "aria-hidden": "true" }, hi));
  };
  const section = (labelText, forId, ...kids) => h("div", { class: "mklsec" }, h("label", { class: "lab", for: forId || null }, labelText), ...kids);

  function briSlider(id, value, onInput) {
    return sliderRow(id, 0, 100, bStep, value, icon("sun"), icon("sun"), onInput, t("makalu_rgb_brightness").replace(/:$/, ""));
  }

  function typeRows(id) {
    const d = S.d;
    const cur = typOf(id);
    const rows = h("div", { class: "typlist", role: "radiogroup", "aria-label": t("mk_typ") });
    for (const ty of TYPES[id]) {
      const on = cur === ty.id;
      const pick = () => {
        if (S.typ[id] === ty.id || (!S.typ[id] && TYPES[id][0].id === ty.id)) return;
        S.typ[id] = ty.id;
        if (ty.id === "dual" && isBlack(d.color2)) d.color2 = [255, 0, 0];
        renderDetail();
        changed();
      };
      const row = h("div", { class: "typrow" + (on ? " on" : ""), role: "radio", "aria-checked": on ? "true" : "false", tabindex: on ? "0" : "-1", "data-type": ty.id },
        h("span", { class: "typname" }, t("mk_type_" + ty.id)));
      if (ty.n >= 1) {
        const c1 = colorInput({ id: "mk-c1-" + ty.id, value: hex(d.color1), "aria-label": ty.n === 2 ? t("makalu_rgb_color1").replace(/:$/, "") : t("mk_color") });
        c1.addEventListener("input", () => { d.color1 = rgb(c1.value); changed(); });
        row.append(c1);
      }
      if (ty.n >= 2) {
        const c2 = colorInput({ id: "mk-c2-" + ty.id, value: hex(d.color2), "aria-label": t("makalu_rgb_color2").replace(/:$/, "") });
        c2.addEventListener("input", () => { d.color2 = rgb(c2.value); changed(); });
        row.append(c2);
      }
      row.addEventListener("click", () => { if (!on) pick(); });
      row.addEventListener("keydown", (e) => { if ((e.key === "Enter" || e.key === " ") && e.target === row) { e.preventDefault(); pick(); } });
      rows.append(row);
    }
    return rows;
  }

  function renderEffect(id) {
    const d = S.d;
    const colA = h("div", { class: "mklcol" });
    const colB = h("div", { class: "mklcol" });
    if (id === "off") {
      colA.append(h("div", { class: "help" }, t("mk_fx_off_help")));
    } else {
      colA.append(section(t("mk_typ"), null, typeRows(id)));
      if (HAS.speed.includes(id)) {
        const i = Math.max(0, speeds.findIndex((s) => s.id === d.speed));
        const sl = sliderRow("mk-speed", 0, speeds.length - 1, 1, i, "▶", "▶▶", (v) => { d.speed = speeds[v].id; sl.querySelector("input").title = speedName(speeds[v]); changed(); }, t("makalu_rgb_speed").replace(/:$/, ""));
        sl.querySelector("input").title = speedName(speeds[i]);
        colB.append(section(t("makalu_rgb_speed").replace(/:$/, ""), "mk-speed", sl));
      }
      colB.append(section(t("makalu_rgb_brightness").replace(/:$/, ""), "mk-bri", briSlider("mk-bri", d.brightness, (v) => { d.brightness = v; changed(); })));
      if (HAS.direction.includes(id)) {
        const seg = h("div", { class: "dirseg", role: "radiogroup", "aria-label": t("makalu_rgb_direction").replace(/:$/, "") });
        const draw = () => {
          clear(seg);
          for (const x of dirs) {
            const left = x.id === "left";
            seg.append(h("button", { type: "button", role: "radio", class: "dirbtn" + (d.direction === x.id ? " on" : ""), "aria-checked": d.direction === x.id ? "true" : "false",
              "aria-label": left ? t("mk_dir_ccw") : t("mk_dir_cw"), title: left ? t("mk_dir_ccw") : t("mk_dir_cw"), "data-dir": x.id,
              onclick: () => { d.direction = x.id; draw(); changed(); } }, left ? "↺" : "↻"));
          }
        };
        draw();
        colB.append(section(t("makalu_rgb_direction").replace(/:$/, ""), null, seg));
      }
    }
    return h("div", { class: "mklbody" }, colA, colB);
  }

  // The eight squares of "Individuell": left column LEDs 0-3 top to bottom, right column LEDs 7-4 top to bottom.
  function buildLeds(ring) {
    const r = ring ? ring.rect : [0.4, 0.28, 0.22, 0.34];
    const layer = h("div", { class: "mkleds", role: "group", "aria-label": t("mk_led_group") });
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("class", "mkleaders");
    svg.setAttribute("aria-hidden", "true");
    layer.append(svg);
    squares = [];
    const mid = r[1] + r[3] / 2, pitch = 0.2;
    LED_OF_SLOT.forEach((led, slot) => {
      const k = slot % 4, right = slot >= 4;
      const ySq = (mid + (k - 1.5) * pitch) * 100;           // percent of the picture's height
      const yRing = (r[1] + (k + 0.5) / 4 * r[3]) * 100;     // the segment this LED lights
      const ln = document.createElementNS("http://www.w3.org/2000/svg", "line");
      ln.setAttribute("x1", right ? "100%" : "0"); ln.setAttribute("x2", (right ? (r[0] + r[2]) * 100 : r[0] * 100) + "%");
      ln.setAttribute("y1", ySq + "%"); ln.setAttribute("y2", yRing + "%");
      svg.append(ln);
      const b = h("button", { type: "button", class: "ledsq" + (right ? " r" : " l"), "data-led": led, style: `top:${ySq}%`,
        "aria-label": t("mk_led_n", { n: led + 1 }), title: t("mk_led_n", { n: led + 1 }), "aria-pressed": "false" });
      b.addEventListener("pointerdown", (e) => {
        if (e.button !== 0) return;
        e.preventDefault();
        if (e.ctrlKey || e.metaKey || e.shiftKey) { if (S.picked.has(led) && S.picked.size > 1) S.picked.delete(led); else S.picked.add(led); }
        else S.picked = new Set([led]);
        dragging = true;
        picksChanged();
      });
      b.addEventListener("pointerenter", () => { if (dragging && !S.picked.has(led)) { S.picked.add(led); picksChanged(); } });
      b.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); if (e.ctrlKey || e.shiftKey) { if (S.picked.has(led) && S.picked.size > 1) S.picked.delete(led); else S.picked.add(led); } else S.picked = new Set([led]); picksChanged(); }
      });
      layer.append(b);
      squares.push({ led, b });
    });
    ledLayer = layer;
    layer.hidden = S.sel !== "custom";
    paintSquares();
    return layer;
  }
  const stopDrag = () => { dragging = false; };
  window.addEventListener("pointerup", stopDrag);
  scope.add(() => window.removeEventListener("pointerup", stopDrag));
  function paintSquares() {
    for (const { led, b } of squares) {
      b.style.background = hex(S.leds[led]);
      const on = S.picked.has(led);
      b.classList.toggle("on", on);
      b.setAttribute("aria-pressed", on ? "true" : "false");
    }
  }
  function picksChanged() {
    paintSquares();
    if (inlinePicker) inlinePicker.value = hex(S.leds[[...S.picked][0]]);
  }

  function renderCustom() {
    dropPicker();
    inlinePicker = colorPanel({
      value: hex(S.leds[[...S.picked][0]]),
      onInput: (c) => { const v = rgb(c); for (const led of S.picked) S.leds[led] = [...v]; paintSquares(); changed(); },
    });
    const reset = h("button", { type: "button", class: "btn mklreset", id: "mk-reset", onclick: () => {
      S.leds = defaultLeds(); paintSquares(); if (inlinePicker) inlinePicker.value = hex(S.leds[[...S.picked][0]]); changed(); } },
      h("span", { "aria-hidden": "true" }, "↺ "), t("mk_reset_led"));
    const colB = h("div", { class: "mklcol" },
      section(t("makalu_rgb_brightness").replace(/:$/, ""), "mk-bri", briSlider("mk-bri", S.ledBri, (v) => { S.ledBri = v; changed(); })),
      h("div", { class: "help" }, t("mk_led_help")),
      h("div", { class: "mklresetrow" }, reset));
    return h("div", { class: "mklbody" }, h("div", { class: "mklcol" }, inlinePicker.el), colB);
  }

  function renderDetail() {
    dropPicker();
    clear(detail);
    detail.append(h("div", { class: "head" }, h("span", { class: "acc" }), h("h2", { class: "t h", style: "margin:0;font-weight:400" }, fxName(S.sel))),
      S.sel === "custom" ? renderCustom() : renderEffect(S.sel));
    if (ledLayer) ledLayer.hidden = S.sel !== "custom";
  }
  function renderAll() { renderList(); renderDetail(); preview(); }

  // The picture centred above the panel; a click on it flashes the ring of "Reaktiv".
  fr.main.append(sb.el, h("div", { class: "mklstage" }, mbox.el), detail);
  mbox.el.addEventListener("click", () => { if (S.sel === "responsive") mbox.flash(); });
  renderAll();
  scope.add(devices.onPhotos(() => mbox.update()));
  await mbox.update();
}

export function unmount() {
  if (flushPending) { flushPending(); flushPending = null; }
  if (scope) scope.dispose();
  scope = null;
}
