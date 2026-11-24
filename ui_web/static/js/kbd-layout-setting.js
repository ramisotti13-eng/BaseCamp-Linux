// The "Keyboard layout" setting of an Everest keyboard: Automatic (what the system says) or one of the
// ten layouts. The core stores it (<device>.set_keyboard_layout); the flat keyboard draws from it.
import bc from "./bridge.js";
import { t, currentLang } from "./i18n.js";
import { h, toast } from "./ui.js";

/** A panel with the select for `device` ("everest_max" or "everest60"). Returns the element. */
export function keyboardLayoutPanel(device, scope) {
  const sel = h("select", { class: "field", id: "kbl-select", "aria-label": t("kbl_label") });
  const note = h("div", { class: "help" }, t("kbl_help"));
  const name = (id) => t("kbl_" + id);

  function fill(info) {
    sel.textContent = "";
    sel.append(h("option", { value: "auto" }, t("kbl_auto", { name: name(info.detected) })));
    for (const l of info.available) sel.append(h("option", { value: l.id }, name(l.id)));
    sel.value = info.choice;
    note.textContent = t("kbl_help") + " " + t("kbl_shape", { shape: info.shape.toUpperCase() });
  }
  sel.addEventListener("change", async () => {
    const r = await bc.call(device + ".set_keyboard_layout", { layout_id: sel.value, lang: currentLang() });
    if (!scope.alive) return;
    if (!r.ok) { toast(t("ui_error", { err: r.error }), "err"); return; }
    fill(r);
    toast(t("kbl_saved"), "ok");
  });
  bc.call(device + ".keyboard_layout", { lang: currentLang() }).then((r) => { if (scope.alive && r.ok) fill(r); });
  return h("div", { class: "panel", style: "margin-top:18px" },
    h("div", { class: "head" }, h("span", { class: "acc" }), h("h2", { class: "t h", style: "margin:0;font-weight:400" }, t("kbl_title"))),
    h("div", { class: "grid2" }, h("div", null, h("label", { class: "lab", for: "kbl-select" }, t("kbl_label")), sel, note)));
}
