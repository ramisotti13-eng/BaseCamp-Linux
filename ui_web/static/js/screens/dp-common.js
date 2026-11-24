// Shared by the DisplayPad screens: the frame with its icon rail, and the status line
// (device absent, upload running).
import bc from "../bridge.js";
import { t } from "../i18n.js";
import * as devices from "../devices.js";
import { h, icon, clear } from "../ui.js";
import { railItem } from "../profiles-page.js";

/** Builds .devpage with the rail; returns {root, col, main}. `withPages` adds the middle column. */
export function frame(el, active, withPages) {
  clear(el);
  const rail = h("nav", { class: "rail", "aria-label": "DisplayPad" },
    railItem("displaypad", active),
    h("a", { href: "#/displaypad/keys", class: active === "keys" ? "on" : "", "aria-current": active === "keys" ? "page" : null }, icon("keys"), t("dp_rail_keys")),
    h("a", { href: "#/displaypad/settings", class: active === "settings" ? "on" : "", "aria-current": active === "settings" ? "page" : null }, icon("sliders"), t("dp_rail_settings")),
    h("a", { href: "#/displaypad/pads", class: active === "pads" ? "on" : "", "aria-current": active === "pads" ? "page" : null }, icon("vpad"), t("vp_rail")));
  const col = withPages ? h("section", { class: "col" }) : null;
  const main = h("section", { class: "main" });
  const root = h("div", { class: "devpage" + (withPages ? "" : " nocol") }, rail, col, main);
  el.append(root);
  return { root, col, main };
}

/** The line above the main area. Shows "not connected" and upload progress, kept up to date from events.
 *  Returns {el, state}; state.present is true / false / null (unknown). */
export function statusBar(scope, onChange) {
  const el = h("div", { class: "status", role: "status" });
  const st = { present: null, uploading: false, n: 0, total: 0, animating: 0 };
  const render = () => {
    clear(el);
    if (st.present === false) el.append(h("span", { class: "chip warn" }, t("no_device_displaypad") + " · " + t("no_device_hint")));
    if (st.uploading) el.append(h("span", { class: "chip" }, st.total ? t("dp_uploading_key", { k: st.n, n: st.n, total: st.total }) : t("dp_uploading")));
    else if (st.animating) el.append(h("span", { class: "chip" }, t("dp_animating", { n: st.animating })));
    if (onChange) onChange(st);
  };
  const num = (v) => (typeof v === "number" ? v : v ? 1 : 0);
  bc.call("displaypad.status").then((r) => {
    if (!scope.alive) return;
    if (r.ok) { st.present = !!r.present; st.uploading = !!r.uploading; st.animating = num(r.animating); }
    else st.present = devices.isPresent("displaypad");
    render();
  });
  scope.add(devices.onChange(() => {
    if (st.present === null) { st.present = devices.isPresent("displaypad"); render(); }
  }));
  scope.on("displaypad.*", (topic, p) => {
    p = p || {};
    switch (topic.slice("displaypad.".length)) {
      case "device_connected": st.present = true; break;
      case "device_disconnected": st.present = false; st.uploading = false; break;
      case "upload_started": st.uploading = true; st.n = 0; st.total = 0; break;
      case "upload_progress": st.uploading = true; st.n = p.n || 0; st.total = p.total || 0; break;
      case "upload_finished": st.uploading = false; break;
      case "animating": st.animating = num(p.count); break;
      default: return;
    }
    render();
  });
  render();
  return { el, state: st };
}
