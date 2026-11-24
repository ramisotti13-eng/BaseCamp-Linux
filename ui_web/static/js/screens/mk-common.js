// Shared by the Makalu screens: the frame with its icon rail, the model, the status line, the result toasts
// and the mouse picture (our drawing or the imported photo, with an optional layer of numbered markers).
import bc from "../bridge.js";
import { t } from "../i18n.js";
import * as devices from "../devices.js";
import { h, icon, clear, toast } from "../ui.js";
import { makaluTop } from "../art.js";
import { MAKALU_PIC, drawingRects, photoRects, lightRing } from "../hotspots.js";
import { createLight } from "../mouse-light.js";
import { railItem } from "../profiles-page.js";

/** Builds .devpage with the rail; returns {root, col, main}. `withCol` adds the middle column. */
export function frame(el, active, withCol, model) {
  clear(el);
  const item = (id, href, ic, label) => h("a", { href, class: active === id ? "on" : "", "aria-current": active === id ? "page" : null }, icon(ic), label);
  const rail = h("nav", { class: "rail", "aria-label": model || "Makalu" },
    railItem("makalu", active),
    item("buttons", "#/makalu/buttons", "mouse", t("mk_rail_buttons")),
    item("lighting", "#/makalu/lighting", "bulb", t("mk_rail_rgb")),
    item("macro", "#/makalu/macro", "macro-m", t("em_rail_macro")),
    item("settings", "#/makalu/settings", "sliders", t("mk_rail_settings")));
  const col = withCol ? h("section", { class: "col" }) : null;
  const main = h("section", { class: "main" });
  const root = h("div", { class: "devpage" + (withCol ? "" : " nocol") }, rail, col, main);
  el.append(root);
  return { root, col, main };
}

/** The mouse's model: {model, is_max, buttons, dpi_min, dpi_max, dpi_step, ...}. Falls back to makalu.status. */
export async function loadModel() {
  let info = await devices.refreshMakalu();
  if (!info) {
    const r = await bc.call("makalu.status");
    if (r && r.ok && r.model) info = r;
  }
  return info || { model: "Makalu 67", is_max: false, buttons: [], dpi_min: 50, dpi_max: 19000, dpi_step: 50 };
}

export const deviceKey = (info) => (info.is_max ? "makalu_max" : "makalu67");

/** "not connected" above the main area, kept up to date from makalu.presence. Returns {el, state}. */
export function statusBar(scope, info) {
  const el = h("div", { class: "status", role: "status" });
  const st = { present: null };
  const render = () => {
    clear(el);
    if (st.present === false) el.append(h("span", { class: "chip warn" }, t("mk_absent", { model: info.model }) + " · " + t("no_device_hint")));
  };
  bc.call("makalu.status").then((r) => {
    if (!scope.alive) return;
    st.present = r && r.ok ? !!r.present : devices.isPresent("makalu67");
    render();
  });
  scope.add(devices.onChange(() => { if (st.present === null) { st.present = devices.isPresent("makalu67"); render(); } }));
  scope.on("makalu.presence", (_t, p) => { st.present = !!(p && p.present); render(); });
  return { el, state: st };
}

const OPS = ["lighting", "custom", "dpi", "remap", "polling_rate", "debounce", "lift_off", "angle_snapping"];

/** makalu.result {op, ok, detail} as a toast, on every Makalu screen. */
export function watchResults(scope, { quiet = false } = {}) {
  scope.on("makalu.result", (_t, p) => {
    p = p || {};
    const op = OPS.includes(p.op) ? t("mk_op_" + p.op) : String(p.op || "");
    if (p.ok) { if (!quiet) toast(op + ": " + t("makalu_applied"), "ok"); }
    else toast(op + ": " + t("makalu_failed") + (p.detail ? " (" + p.detail + ")" : ""), "err");
  });
}

/** Translated label of a remap function id ("dpi+" -> "DPI+") and of a stored assignment ("sniper:800"). */
export function fnLabel(fn) {
  const key = { "dpi+": "dpi_plus", "dpi-": "dpi_minus" }[fn] || fn;
  return t("makalu_remap_fn_" + key);
}
export function assignmentLabel(a) {
  if (typeof a !== "string") return "";
  if (a.startsWith("sniper:")) return fnLabel("sniper") + " · " + a.slice(7) + " DPI";
  return fnLabel(a);
}

/** The picture of the mouse in a .mkbox. `markers`, when given, is [{id, label, cls, pressed, onclick}] in the order of
 *  info.buttons; they are laid over the picture as round numbered buttons. `decorate(ring)` may return one more node
 *  laid over it (the lighting page's LED squares). Photo or drawing follows the imported
 *  pictures (a photo only when a hotspot table exists for its very source file, see hotspots.js).
 *  Returns {el, update()} where update() re-checks the photos (call it after devices.onPhotos). */
export function mouseBox(info, { markers = null, size = "", decorate = null } = {}) {
  const key = deviceKey(info);
  const pic = MAKALU_PIC[key];
  const box = h("div", { class: "mkbox " + size, style: `aspect-ratio:${pic.w}/${pic.h}` });
  let photo = null;   // {url, rects, source}
  let light = null;   // the ring light layer of the current picture (mouse-light.js)
  let lit = null;     // {state, opts}: what the ring shows, kept across redraws

  const draw = () => {
    const focus = box.contains(document.activeElement) ? document.activeElement.getAttribute("data-id") : null;
    clear(box);
    // The ring light: behind a see-through photo, else a ring-shaped stroke over the picture (hotspots.js RINGS).
    const ring = lightRing(key, "page", photo ? photo.source : null);
    const [pw, ph] = ring ? ring.pic : [pic.w, pic.h];
    box.style.aspectRatio = `${pw}/${ph}`;
    box.classList.toggle("ml-lit-behind", !!ring && ring.mode === "behind");
    if (light) light.dispose();
    light = ring ? createLight(ring) : null;
    if (light) light.set(lit && lit.state, lit && lit.opts);
    let picture;
    if (photo) picture = h("img", { class: "mkpic", src: photo.url, alt: "", draggable: "false" });
    else {
      const tpl = document.createElement("template");
      tpl.innerHTML = makaluTop(info.is_max).trim();
      picture = tpl.content.firstChild;
      picture.setAttribute("class", "mkpic");
    }
    if (light && ring.mode === "behind") box.append(light.el, picture);
    else box.append(picture, ...(light ? [light.el] : []));
    if (decorate) { const extra = decorate(ring); if (extra) box.append(extra); }
    if (markers) {
      const rects = photo ? photo.rects : drawingRects(key, "page");
      const layer = h("div", { class: "hotspots", role: "group", "aria-label": t("mk_buttons_label") });
      markers.forEach((m, i) => {
        const r = rects && rects[i];
        if (!r) return;
        layer.append(h("button", { type: "button", class: "mk " + (m.cls || ""), "data-id": m.id, "aria-label": m.label,
          "aria-pressed": m.pressed ? "true" : "false", title: m.label,
          style: `left:${r[0] * 100}%;top:${r[1] * 100}%;width:${r[2] * 100}%;height:${r[3] * 100}%`, onclick: m.onclick },
          h("span", { "aria-hidden": "true" }, m.id)));
      });
      box.append(layer);
    }
    if (focus != null) { const b = box.querySelector(`.mk[data-id="${focus}"]`); if (b) b.focus(); }
  };

  const update = async () => {
    const rects = photoRects(key, "page", devices.photoSource("makalu67", "page"));
    const url = rects ? await devices.photoUrl("makalu67", "page", 900) : null;
    const next = rects && url ? { url, rects, source: devices.photoSource("makalu67", "page") } : null;
    if ((next && next.url) !== (photo && photo.url)) { photo = next; draw(); }
  };
  draw();
  return { el: box, redraw: draw, update, setMarkers(m) { markers = m; draw(); },
    /** The ring's light: a lighting state {effect, speed, direction, brightness, color1, color2, leds}, or null for
     *  none. {still: true} draws one frame without animation. */
    setLight(state, opts) { lit = state ? { state, opts } : null; if (light) light.set(state, opts); },
    /** The "responsive" light flashes once (a click on the picture). */
    flash() { if (light) light.flash(); } };
}

/** Shows the saved lighting (one still frame) on a mouseBox; the Buttons and Settings screens use it so the ring is
 *  never a dark hole. `alive()` tells whether the screen is still open. */
export async function showSavedLight(mbox, alive) {
  const r = await bc.call("makalu.lighting");
  if (!r || !r.ok || !alive()) return;
  const state = { effect: r.effect, speed: r.speed, direction: r.direction, brightness: r.brightness, color1: r.color1, color2: r.color2 };
  if (r.effect === "custom") {
    const c = await bc.call("makalu.custom_leds");
    if (!alive()) return;
    if (c && c.ok) { state.leds = c.leds; if (c.brightness != null) state.brightness = c.brightness; }
  }
  mbox.setLight(state, { still: true });
}
