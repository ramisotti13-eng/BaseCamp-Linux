// Everest Max settings: whether the numpad and the media dock are attached. The keyboard's own answer is
// not always there, so each can be set by hand; Automatic means "as the keyboard says, else assumed".
import bc from "./bridge.js";
import { t } from "./i18n.js";
import { h, toast } from "./ui.js";

export function modulesPanel(scope) {
  const mk = (id, label) => {
    const sel = h("select", { class: "field", id }, ["auto", "on", "off"].map((v) => h("option", { value: v }, t("mod_" + v))));
    return { sel, field: h("div", null, h("label", { class: "lab", for: id }, label), sel) };
  };
  const numpad = mk("mod-numpad", t("mod_numpad")), dock = mk("mod-dock", t("mod_dock"));
  const note = h("div", { class: "help clamp", title: t("mod_help") }, t("mod_help"));   // two lines in low windows, whole on hover
  const show = (r) => {
    numpad.sel.value = r.setting.numpad; dock.sel.value = r.setting.dock;
    note.textContent = note.title = t("mod_help") + " " + t("mod_source_" + r.source);
  };
  const save = async () => {
    const r = await bc.call("everest_max.set_modules_setting", { numpad: numpad.sel.value, dock: dock.sel.value });
    if (!scope.alive) return;
    if (!r.ok) { toast(t("ui_error", { err: r.error }), "err"); return; }
    show(r);
  };
  numpad.sel.addEventListener("change", save);
  dock.sel.addEventListener("change", save);
  bc.call("everest_max.modules").then((r) => { if (scope.alive && r.ok) show(r); });
  return h("div", { class: "panel", style: "margin-top:18px" },
    h("div", { class: "head" }, h("span", { class: "acc" }), h("h2", { class: "t h", style: "margin:0;font-weight:400" }, t("mod_title"))),
    h("div", { class: "grid2" }, numpad.field, dock.field), note);
}
