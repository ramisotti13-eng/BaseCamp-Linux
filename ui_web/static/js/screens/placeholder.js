// "Coming later" screen for devices that are not built yet. (The settings pages are set-*.js.)
import { t } from "../i18n.js";
import { byId } from "../devices.js";
import { h, clear } from "../ui.js";

export async function mount(el, ctx) {
  clear(el);
  const d = byId(ctx.id);
  el.append(h("div", { class: "soon" }, h("div", null,
    h("h1", { class: "h" }, d ? d.name : ctx.id),
    h("p", null, t("shell_device_later")),
    h("a", { class: "cta inline", href: "#/home" }, t("ui_back")))));
}

export function unmount() {}
