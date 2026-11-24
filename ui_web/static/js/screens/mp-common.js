// Shared by the MacroPad screens: the frame with its icon rail, the status line (pad absent, what the
// device thread reported), the action catalogue, and the pad picture with its layer of 12 keys.
import bc from "../bridge.js";
import { t } from "../i18n.js";
import * as devices from "../devices.js";
import { PAGE_ART } from "../art.js";
import { drawingRects, photoRects, PAD_DRAWING } from "../hotspots.js";
import { h, icon, clear } from "../ui.js";
import { toHex } from "../rgbkit.js";

export const NUM_KEYS = 12;

/** Builds .devpage with the rail; returns {root, col, main}. `withCol` adds the middle column. */
export function frame(el, active, withCol) {
  clear(el);
  const link = (id, href, ic, label) => h("a", { href, class: active === id ? "on" : "", "aria-current": active === id ? "page" : null }, icon(ic), label);
  const rail = h("nav", { class: "rail", "aria-label": "MacroPad" },
    link("keys", "#/macropad/keys", "keys", t("dp_rail_keys")),
    link("rgb", "#/macropad/rgb", "bulb", t("mp_rail_rgb")));
  const col = withCol ? h("section", { class: "col" }) : null;
  const main = h("section", { class: "main" });
  const root = h("div", { class: "devpage" + (withCol ? "" : " nocol") }, rail, col, main);
  el.append(root);
  return { root, col, main };
}

/** The error text the core gives for "no pad" in the page's language. */
export const errText = (err) => (/not connected/i.test(String(err)) ? t("no_device_macropad") : t("ui_error", { err }));

/** The status codes of macropad.status, in words: {text, kind} (kind: "" | "ok" | "err"), or null. */
export function statusText(code, detail) {
  switch (code) {
    case "ready": return { text: t("mp_ready"), kind: "ok" };
    case "applied": return { text: t("rgb_applied"), kind: "ok" };
    case "saved": return { text: t("mp_saved"), kind: "ok" };
    case "job_failed": return { text: t("ui_error", { err: detail }), kind: "err" };
    case "open_failed": return { text: t("mp_open_failed", { err: detail }), kind: "err" };
    case "failed": return { text: t("mp_failed", { err: detail }), kind: "err" };
    default: return null;
  }
}

/** The line above the main area. Returns {el, state}; state.present is true / false / null. */
export function statusBar(scope, onChange) {
  const el = h("div", { class: "status", role: "status" });
  const st = { present: null, last: null };
  const render = () => {
    clear(el);
    if (st.present === false) el.append(h("span", { class: "chip warn" }, t("no_device_macropad") + " · " + t("no_device_hint")));
    else if (st.last) el.append(h("span", { class: "chip" + (st.last.kind === "err" ? " warn" : "") }, st.last.text));
    if (onChange) onChange(st);
  };
  bc.call("macropad.status").then((r) => {
    if (!scope.alive) return;
    if (r.ok) {
      st.present = !!r.present;
      if (r.present && r.worker && !st.last) st.last = statusText("ready");
    } else st.present = devices.isPresent("macropad");
    render();
  });
  scope.add(devices.onChange(() => {
    if (st.present === null) { st.present = devices.isPresent("macropad"); render(); }
  }));
  scope.on("macropad.presence", (_t, p) => { st.present = !!(p && p.present); if (!st.present) st.last = null; render(); });
  scope.on("macropad.status", (_t, p) => { st.last = statusText(p && p.code, p && p.detail); render(); });
  render();
  return { el, state: st };
}

// ── the action catalogue ─────────────────────────────────────────────────────

// Used when the catalogue is not there: the types the pad offers, with plain text values.
const HINTS = { shell: "mp_hint_shell", url: "mp_hint_url", folder: "mp_hint_folder", app: "mp_hint_app", obs: "mp_hint_obs",
  macro: "mp_hint_macro", keypress: "mp_hint_keypress", text: "action_type_text_hint" };

/** [{id, label, kind, browse?, options?, hint}] for the types macropad.actions offers. The value kinds and the
 *  lists (OBS scenes, macros) come from the shared catalogue (displaypad.action_types), which knows them. */
export async function loadTypes() {
  const [ar, cr] = await Promise.all([bc.call("macropad.actions"), bc.call("displaypad.action_types")]);
  const ids = ar.ok && Array.isArray(ar.types) ? ar.types : ["none", "shell", "url", "folder", "app", "obs", "macro", "keypress", "text"];
  const cat = new Map();
  const list = cr.ok ? (cr.types || cr.action_types || []) : [];
  for (const e of list) if (e && e.id) cat.set(e.id, e);
  return {
    actions: ar.ok && Array.isArray(ar.actions) ? ar.actions : [],
    types: ids.map((id) => {
      const e = cat.get(id) || {};
      const key = "action_type_" + id;
      const label = t(key) === key ? (e.label || (id === "obs" ? "OBS" : id)) : t(key);
      const kind = id === "none" ? "none" : (e.kind || "text");
      return { id, label, kind, browse: e.browse || (id === "folder" ? "folder" : id === "app" ? "file" : null),
        options: Array.isArray(e.options) ? e.options.map((o) => ({ value: String(o.value), label: String(o.label ?? o.value) })) : null,
        hint: HINTS[id] ? t(HINTS[id]) : "" };
    }),
  };
}

// ── the pad picture ──────────────────────────────────────────────────────────

/** Whether a photo with a measured hotspot table is in use: {url, rects} or null. */
export async function padPhoto() {
  const rects = photoRects("macropad", "page", devices.photoSource("macropad", "page"));
  const url = rects ? await devices.photoUrl("macropad", "page", 900) : null;
  return url && rects ? { url, rects } : null;
}

/**
 * Draws the pad (photo or drawing) into `host` with one button per key over it.
 *   key(i) -> {assigned, text, color?}  what the key shows: `text` in the face, `color` ([r,g,b]) as its background
 *   sel    selected index or null;  pressed: index being pressed on the pad or null
 *   onSelect(i), onClear(i)  (onClear optional: right-click)
 */
export function drawPad(host, { photo, key, sel, pressed, onSelect, onClear, label }) {
  const focusIdx = host.contains(document.activeElement) ? document.activeElement.getAttribute("data-idx") : null;
  const rects = photo ? photo.rects : drawingRects("macropad", "page");
  clear(host);
  let picture;
  if (photo) picture = h("img", { class: "padpic", src: photo.url, alt: "", draggable: "false" });
  else {
    const tpl = document.createElement("template");
    tpl.innerHTML = PAGE_ART.macropad().trim();     // our own static drawing
    picture = tpl.content.firstChild;
    picture.setAttribute("viewBox", `0 0 ${PAD_DRAWING.w} ${PAD_DRAWING.h}`);
  }
  const layer = h("div", { class: "hotspots", role: "group", "aria-label": label });
  for (let i = 0; i < NUM_KEYS; i++) {
    const [x, y, w, hh] = rects[i];
    const k = key(i);
    const style = `left:${x * 100}%;top:${y * 100}%;width:${w * 100}%;height:${hh * 100}%`;
    const face = h("span", { class: "face" }, h("span", { class: "lbl mlab" }, "M" + (i + 1)), k.text ? h("span", { class: "lbl mtext" }, k.text) : null);
    if (k.color) {
      face.style.background = toHex(k.color);
      face.classList.add((0.299 * k.color[0] + 0.587 * k.color[1] + 0.114 * k.color[2]) > 140 ? "lightface" : "darkface");
    }
    layer.append(h("button", { type: "button",
      class: "hs mpkey" + (k.assigned ? " assigned" : "") + (sel === i ? " sel" : "") + (pressed === i ? " pressed" : "") + (photo ? " onphoto" : "") + (k.color ? " colored" : ""),
      "data-idx": i, "aria-label": k.aria, "aria-pressed": sel === i ? "true" : "false", style,
      onclick: () => onSelect(i), oncontextmenu: (e) => { if (onClear) { e.preventDefault(); onClear(i); } } }, face));
  }
  host.append(h("div", { class: "padbox mp" + (photo ? " photo" : "") }, picture, layer));
  if (focusIdx != null) { const b = host.querySelector(`.hs[data-idx="${focusIdx}"]`); if (b) b.focus(); }
}
