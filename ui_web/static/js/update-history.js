// The version history: what each version brought, from the shipped
// CHANGELOG.md, with an update's own release notes on top when one is
// waiting. Opened from Settings > Update and from the "updated" message.
import bc from "./bridge.js";
import { t, currentLang } from "./i18n.js";
import { h, modal, toast } from "./ui.js";
import { renderMarkdown } from "./markdown.js";

const base = (v) => String(v || "").replace(/^v/i, "").split("-")[0];

/** One version as a collapsible section. */
function entry({ version, date, body, badge, open }) {
  const head = h("summary", null,
    h("span", { class: "vh-ver h" }, version),
    date ? h("span", { class: "vh-date" }, date) : null,
    badge ? h("span", { class: "badge vh-badge" }, badge) : null);
  const det = h("details", { class: "vh-entry" }, head,
    renderMarkdown(body, { onLink: (url) => bc.openExternal(url) }));
  if (open) det.open = true;
  return det;
}

/** Open the history. `focus`: the version to open first (default: the
 *  update if one is waiting, else the installed version). */
export async function openHistory({ focus } = {}) {
  const r = await bc.call("update.history");
  if (!r || !r.ok) { toast(t("ui_error", { err: (r && r.error) || "update.history" }), "err"); return; }
  const installed = base(r.installed);
  const want = base(focus || (r.update && r.update.version) || r.installed);
  const list = h("div", { class: "vh-list" });
  if (r.update && r.update.notes) {
    list.append(entry({ version: r.update.version, body: r.update.notes, badge: t("upd_history_available"),
      open: base(r.update.version) === want }));
  }
  for (const e of r.entries || []) {
    if (r.update && base(e.version) === base(r.update.version) && r.update.notes) continue;
    list.append(entry({ version: e.version, date: e.date, body: e.body,
      badge: base(e.version) === installed ? t("upd_history_installed") : null,
      open: base(e.version) === want }));
  }
  if (!list.children.length) list.append(h("p", { class: "help" }, t("upd_history_empty")));
  const note = currentLang() !== "en" ? h("div", { class: "help vh-note" }, t("upd_history_english")) : null;
  const { dlg } = modal({
    title: t("upd_history_title"),
    body: h("div", { class: "vh" }, note, list),
    buttons: (close) => [h("button", { class: "btn primary", type: "button", onclick: () => close(null) }, t("ui_close"))],
    onClose: () => {},
  });
  dlg.classList.add("wide");
  // An entry further down (an older version asked for) is scrolled to; the
  // first one stays where it is, under the note.
  const opened = list.querySelector("details[open]");
  if (opened && opened !== list.firstElementChild) opened.scrollIntoView({ block: "start" });
}
