// Shared by the screens under the gear (Settings, Macros, OBS, Plugins, Remote access): the frame
// with its icon rail, a titled panel, and the wording of errors. These tabs have no device, so they
// sit together under the gear, the way the device pages keep their own rail.
import { t } from "../i18n.js";
import { h, icon, clear } from "../ui.js";
import * as shell from "../shell-state.js";

const TABS = [
  { id: "settings", href: "#/settings", icon: "gear", label: () => t("set_rail_general") },
  { id: "macros", href: "#/settings/macros", icon: "macro", label: () => t("macro_title") },
  { id: "obs", href: "#/settings/obs", icon: "obs", label: () => "OBS" },
  { id: "plugins", href: "#/settings/plugins", icon: "plug", label: () => t("dp_plugins_group") },
  { id: "remote", href: "#/settings/remote", icon: "globe", label: () => t("rset_rail") },
];

/** Builds .devpage with the rail; returns {root, col, main}. `withList` adds the middle column. */
export function frame(el, active, withList) {
  clear(el);
  const rail = h("nav", { class: "rail", "aria-label": t("settings_title") },
    TABS.map((tab) => h("a", { href: tab.href, class: tab.id === active ? "on" : "", "aria-current": tab.id === active ? "page" : null },
      icon(tab.icon), tab.label(),
      tab.id === "plugins" && shell.state.plugins ? h("span", { class: "bdg inline", title: t(shell.state.plugins === 1 ? "plugin_updates_one" : "plugin_updates_many", { n: shell.state.plugins }) }, String(shell.state.plugins)) : null)));
  const col = withList ? h("section", { class: "col" }) : null;
  const main = h("section", { class: "main" });
  const root = h("div", { class: "devpage" + (withList ? "" : " nocol") }, rail, col, main);
  el.append(root);
  return { root, col, main };
}

/** A card with the blue bar and a title; the rest is its body. */
export function panel(title, ...kids) {
  return h("section", { class: "panel", "aria-label": title },
    h("div", { class: "head" }, h("span", { class: "acc" }), h("h2", { class: "t h", style: "margin:0;font-weight:400" }, title)),
    h("div", { class: "pbody" }, kids));
}

export const errText = (r) => t("ui_error", { err: (r && r.error) || "?" });

/** The head of a list column: a blue bar and the title. */
export function colHead(title) {
  return h("div", { class: "head" }, h("span", { class: "acc" }), h("h2", { class: "t h", style: "margin:0;font-weight:400" }, title));
}

export function iconBtn(name, label, fn, disabled) {
  return h("button", { class: "iconbtn", type: "button", title: label, "aria-label": label, disabled, onclick: fn }, icon(name));
}
