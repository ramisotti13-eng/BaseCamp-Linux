// Shared by the Everest 60 screens: the frame with its icon rail, the status line (board absent,
// model), and the toasts for what the controller answered.
import bc from "../bridge.js";
import { t } from "../i18n.js";
import * as devices from "../devices.js";
import { h, icon, clear, toast } from "../ui.js";

/** Builds .devpage with the rail; returns {root, col, main}. `withCol` adds the middle column. */
export function frame(el, active, withCol) {
  clear(el);
  const link = (id, href, ic, label) => h("a", { href, class: active === id ? "on" : "", "aria-current": active === id ? "page" : null }, icon(ic), label);
  const rail = h("nav", { class: "rail", "aria-label": "Everest 60" },
    link("rgb", "#/everest60/rgb", "bulb", t("e60_rail_rgb")),
    link("settings", "#/everest60/settings", "sliders", t("e60_rail_settings")));
  const col = withCol ? h("section", { class: "col" }) : null;
  const main = h("section", { class: "main" });
  const root = h("div", { class: "devpage" + (withCol ? "" : " nocol") }, rail, col, main);
  el.append(root);
  return { root, col, main };
}

/** "ISO" or "ANSI" from the model name the core reports ("Everest 60 ISO"). */
export const layoutName = (model) => (/iso/i.test(String(model || "")) ? "ISO" : "ANSI");

/** The line above the main area: "not connected" and the model. Returns {el, state}. */
export function statusBar(scope, onChange) {
  const el = h("div", { class: "status", role: "status" });
  const st = { present: null, model: "Everest 60", busy: false };
  const render = () => {
    clear(el);
    if (st.present === false) el.append(h("span", { class: "chip warn" }, t("no_device_keyboard") + " · " + t("no_device_hint")));
    else if (st.present) el.append(h("span", { class: "chip" }, st.model + " · " + t("e60_layout_is", { layout: layoutName(st.model) })));
    if (st.busy) el.append(h("span", { class: "chip" }, t("rgb_applying")));
    if (onChange) onChange(st);
  };
  (async () => {
    // detect reads the model off the HID bus (ANSI or ISO); status says what the core knows.
    const d = await bc.call("everest60.detect");
    const r = await bc.call("everest60.status");
    if (!scope.alive) return;
    if (r.ok) { st.present = !!r.present; st.model = r.model || st.model; st.busy = !!r.busy; }
    else st.present = devices.isPresent("everest60");
    if (d.ok && d.found && d.model) st.model = d.model;
    render();
  })();
  scope.add(devices.onChange(() => {
    if (st.present === null) { st.present = devices.isPresent("everest60"); render(); }
  }));
  scope.on("everest60.presence", (_t, p) => {
    st.present = !!(p && p.present);
    if (p && p.model) st.model = p.model;
    render();
  });
  render();
  return { el, state: st };
}

/** What the controller answered, as a toast: everest60.rgb_applied {op, ok, detail}. */
export function watchApplied(scope, onResult) {
  const okText = { lighting: "rgb_applied", saved: "rgb_applied", side_ring: "side_leds_applied", per_key: "custom_rgb_applied" };
  scope.on("everest60.rgb_applied", (_t, p) => {
    if (!p) return;
    if (p.ok) toast(t(okText[p.op] || "rgb_applied"), "ok");
    else toast(t("ui_error", { err: p.detail || t("rgb_error") }), "err");
    if (onResult) onResult(p);
  });
}
