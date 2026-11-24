// Everest 60, settings: which board this is (ANSI or ISO), whether it is connected, and the saved
// lighting pushed again on request.
import bc from "../bridge.js";
import { t } from "../i18n.js";
import * as devices from "../devices.js";
import { h, clear, toast, makeScope } from "../ui.js";
import { frame, statusBar, watchApplied, layoutName } from "./e60-common.js";
import { keyboardLayoutPanel } from "../kbd-layout-setting.js";

let scope = null;

export async function mount(el) {
  scope = makeScope();
  const fr = frame(el, "settings", false);
  let ready = false;
  const sb = statusBar(scope, () => { if (ready) render(); });
  watchApplied(scope);
  const host = h("div", { style: "width:100%" });
  fr.main.append(sb.el, host);

  const row = (label, value) => h("div", null, h("span", { class: "lab" }, label), h("div", { class: "infoval" }, value));

  function render() {
    const st = sb.state;
    clear(host);
    const present = st.present === null ? devices.isPresent("everest60") : st.present;
    const reapply = h("button", { class: "btn primary", type: "button", disabled: !present, onclick: async () => {
      const r = await bc.call("everest60.apply_saved");
      if (!r.ok) toast(t("ui_error", { err: r.error }), "err");
      else if (!r.sent) toast(t("e60_nothing_saved"));
    } }, t("e60_reapply"));
    const again = h("button", { class: "btn", type: "button", onclick: async () => {
      const d = await bc.call("everest60.detect");
      if (d.ok && d.found) { st.model = d.model; render(); }
      else toast(t("no_device_keyboard"), "err");
    } }, t("e60_detect"));
    host.append(h("div", { class: "panel" },
      h("div", { class: "head" }, h("span", { class: "acc" }), h("h3", { class: "t h", style: "margin:0;font-weight:400" }, t("e60_info_title"))),
      h("div", { class: "grid2" },
        row(t("e60_info_model"), st.model),
        row(t("e60_info_layout"), layoutName(st.model)),
        row(t("e60_info_state"), present ? t("e60_connected") : t("home_not_connected"))),
      h("p", { class: "help" }, t("e60_layout_help")),
      h("div", { class: "actions" }, reapply, again)), keyboardLayoutPanel("everest60", scope));
  }
  ready = true;
  render();
}

export function unmount() { if (scope) scope.dispose(); scope = null; }
