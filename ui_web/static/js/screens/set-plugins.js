// Plugins: the installed ones with a status dot, the published ones from the index,
// Update all, install from a folder or GitHub, and a detail page per plugin.
// A plugin's CustomTkinter panel cannot be drawn here; its detail page says so.
import bc from "../bridge.js";
import { t } from "../i18n.js";
import { h, icon, clear, toast, makeScope, confirmDialog } from "../ui.js";
import { frame, panel, colHead, errText } from "./set-common.js";

let scope = null;

const versionTuple = (s) => String(s || "").replace(/^v/, "").split(".").map((p) => parseInt(p.replace(/\D/g, ""), 10) || 0);
function cmpVersion(a, b) {
  const x = versionTuple(a), y = versionTuple(b);
  for (let i = 0; i < Math.max(x.length, y.length); i++) {
    const d = (x[i] || 0) - (y[i] || 0);
    if (d) return d < 0 ? -1 : 1;
  }
  return 0;
}

/** The sentence for a refused install, from the code the core replied with. */
function describe(r) {
  const p = r.params || {};
  const lead = p.label ? p.label + ": " : "";
  switch (r.code) {
    case "is_link": return lead + t("pluginmgr_is_link", { path: p.path, target: p.target });
    case "other_copy": return lead + t("pluginmgr_other_copy", { path: p.path });
    case "no_manifest": return lead + t("set_plugin_err_no_manifest");
    case "no_id": return lead + t("set_plugin_err_no_id");
    case "bad_source": return t("set_plugin_err_bad_source");
    default: return r.error || r.message || "?";
  }
}

const dotOf = (status) => (status === "info" ? "update" : status || "off");

function statusText(p) {
  switch (p.status) {
    case "off": return t("pluginmgr_disabled");
    case "bad": return p.error ? t("pluginmgr_error") + ": " + p.error : t("pluginmgr_not_loaded");
    case "warn": return t("set_plugin_needs");
    case "info": return t("pluginmgr_update_to", { ver: p.update });
    default: return t("pluginmgr_active");
  }
}

export async function mount(el) {
  scope = makeScope();
  const fr = frame(el, "plugins", true);
  const S = {
    installed: [], index: [], indexState: "idle", indexErr: "",
    sel: { kind: "install" },     // {kind: "installed"|"avail"|"install", id}
    busy: false, installMsg: null, lastFailures: [],
  };
  const statusHost = h("div", { class: "status", role: "status" });
  const detailHost = h("div", { style: "width:100%" });
  fr.main.append(statusHost, detailHost);

  const byId = (id) => S.installed.find((p) => p.id === id);
  const published = (id) => S.index.find((p) => p.id === id);
  const availList = () => S.index.filter((p) => !byId(p.id));
  const updatable = () => S.installed.filter((p) => p.update);

  // ── data ──────────────────────────────────────────────────────────────────
  async function loadInstalled() {
    const r = await bc.call("plugins.list");
    if (!scope.alive) return;
    if (!r.ok) { toast(errText(r), "err"); return; }
    S.installed = r.plugins || [];
    if (S.sel.kind === "installed" && !byId(S.sel.id)) S.sel = { kind: "install" };
    renderAll();
  }

  async function loadIndex() {
    S.indexState = "loading";
    renderAll();
    const r = await bc.call("plugins.fetch_index");
    if (!scope.alive) return;
    if (r.ok) { S.index = r.plugins || []; S.indexState = "idle"; S.indexErr = ""; }
    else { S.indexState = "error"; S.indexErr = r.error || "?"; }
    await loadInstalled();      // statuses know about updates now
  }

  function choose(sel) { S.sel = sel; S.installMsg = null; renderAll(); }

  // ── actions ────────────────────────────────────────────────────────────────
  async function install(source, then) {
    if (S.busy) return;
    S.busy = true; S.installMsg = { kind: "wait", text: t("pluginmgr_installing") };
    renderAll();
    const r = await bc.call("plugins.install", { source });
    S.busy = false;
    if (!scope.alive) return;
    if (!r.ok) {
      S.installMsg = { kind: "err", text: t("pluginmgr_install_fail", { err: describe(r) }) };
      renderAll();
      return;
    }
    S.installMsg = { kind: "ok", text: t("pluginmgr_install_ok") };
    toast(t("pluginmgr_install_ok"), "ok");
    await loadInstalled();
    if (then) then(r.id);
    else if (r.id && byId(r.id)) choose({ kind: "installed", id: r.id });
  }

  async function updateAll() {
    if (S.busy) return;
    S.busy = true; S.lastFailures = [];
    renderAll();
    const r = await bc.call("plugins.update_all");
    S.busy = false;
    if (!scope.alive) return;
    if (!r.ok) { toast(errText(r), "err"); renderAll(); return; }
    S.lastFailures = (r.failures || []).map((f) => describe({ code: f.code, error: f.message, params: { ...(f.params || {}), label: f.label } }));
    if (S.lastFailures.length) {
      const text = t("pluginmgr_install_fail", { err: S.lastFailures.join("; ") });
      toast(r.installed ? t("pluginmgr_install_ok") + " " + text : text, "err");
    } else toast(t("pluginmgr_install_ok"), "ok");
    await loadInstalled();
  }

  async function simple(cmd, id, okText) {
    const r = await bc.call("plugins." + cmd, { id });
    if (!r.ok) return toast(errText(r), "err");
    if (okText) toast(okText, "ok");
    await loadInstalled();
  }

  async function uninstall(p) {
    const ok = await confirmDialog({ title: t("pluginmgr_uninstall"), message: t("set_plugin_uninstall_confirm", { name: p.name }), okLabel: t("pluginmgr_uninstall"), danger: true });
    if (!ok) return;
    const r = await bc.call("plugins.uninstall", { id: p.id });
    if (!r.ok) return toast(errText(r), "err");
    S.sel = { kind: "install" };
    await loadInstalled();
  }

  async function browse(input) {
    const path = await bc.pickFile({ title: t("pluginmgr_pick_folder"), kind: "folder" });
    if (path) { input.value = path; input.focus(); }
  }

  // ── the list ──────────────────────────────────────────────────────────────
  function row(p, kind, sub, dot) {
    const on = S.sel.kind === kind && S.sel.id === p.id;
    return h("div", { class: "row" + (on ? " on" : "") },
      h("button", { class: "pick", type: "button", "data-f": kind + p.id, "aria-pressed": on ? "true" : "false", onclick: () => choose({ kind, id: p.id }) },
        h("div", { class: "tile" }, dot ? h("span", { class: "dot big " + dot, title: statusText(p), "aria-hidden": "true" }) : icon("plug")),
        h("div", { class: "txt" },
          h("div", { class: "nm h" }, p.name || p.id),
          h("div", { class: "sub" }, sub, dot ? h("span", { class: "sr-only" }, " " + statusText(p)) : null))));
  }

  function renderList() {
    const col = fr.col;
    const keep = col.contains(document.activeElement) ? document.activeElement.getAttribute("data-f") : null;
    clear(col);
    const n = updatable().length;
    col.append(
      colHead(t("dp_plugins_group")),
      h("button", { class: "newbtn", type: "button", onclick: () => choose({ kind: "install" }) }, icon("plus"), t("pluginmgr_install")));
    if (n) col.append(h("button", { class: "btn", type: "button", style: "margin:0 14px 6px", disabled: S.busy, onclick: updateAll },
      S.busy ? t("pluginmgr_installing") : t("pluginmgr_update_all", { n })));
    const rows = h("div", { class: "rows" });
    rows.append(h("div", { class: "sect" }, t("pluginmgr_installed") + " (" + S.installed.length + ")"));
    if (!S.installed.length) rows.append(h("div", { class: "empty" }, t("plugin_no_plugins")));
    for (const p of S.installed) {
      rows.append(row(p, "installed",
        ["v" + p.version, p.update ? h("span", { class: "badge", style: "margin-left:6px" }, t("pluginmgr_update_badge", { ver: p.update })) : null], dotOf(p.status)));
    }
    rows.append(h("div", { class: "sect row-sect" }, t("pluginmgr_available"),
      h("button", { class: "iconbtn", type: "button", title: t("set_plugin_refresh"), "aria-label": t("set_plugin_refresh"), disabled: S.indexState === "loading", onclick: loadIndex }, icon("refresh"))));
    if (S.indexState === "loading") rows.append(h("div", { class: "empty" }, t("pluginmgr_loading")));
    else if (S.indexState === "error") rows.append(h("div", { class: "empty err" }, t("pluginmgr_fetch_fail", { err: S.indexErr })));
    else if (!availList().length) rows.append(h("div", { class: "empty" }, t("pluginmgr_no_available")));
    for (const p of availList()) rows.append(row(p, "avail", "v" + p.version, null));
    col.append(rows);
    if (keep) { const f = col.querySelector(`[data-f="${keep}"]`); if (f) f.focus(); }
  }

  // ── the main area ─────────────────────────────────────────────────────────
  function typeBadges(types) {
    return h("span", { class: "types" }, (types || []).filter(Boolean).map((ty) => h("span", { class: "type" }, ty)));
  }

  function msgBox() {
    const m = S.installMsg;
    if (!m) return null;
    return h("div", { class: "msg " + m.kind, role: m.kind === "err" ? "alert" : "status" }, m.text);
  }

  function renderInstall() {
    const input = h("input", { class: "field", id: "pl-src", type: "text", autocomplete: "off", spellcheck: "false", placeholder: t("pluginmgr_install_url") });
    const go = () => { const v = input.value.trim(); if (v) install(v); };
    input.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); go(); } });
    const failures = S.lastFailures.length
      ? h("div", { class: "msg err", role: "alert" }, t("pluginmgr_install_fail", { err: S.lastFailures.join("; ") })) : null;
    detailHost.append(panel(t("pluginmgr_install"),
      h("div", { class: "help", style: "margin:14px 0 14px" }, t("pluginmgr_install_hint")),
      h("label", { class: "lab", for: "pl-src" }, t("pluginmgr_install_url")),
      h("div", { class: "inline-row" }, input,
        h("button", { class: "btn", type: "button", onclick: () => browse(input) }, icon("folder"), t("pluginmgr_install_browse")),
        h("button", { class: "btn primary", type: "button", disabled: S.busy, onclick: go }, t("pluginmgr_install_btn"))),
      msgBox(), failures,
      h("div", { class: "help", style: "margin-top:18px" }, t("pluginmgr_more"))));
  }

  function renderInstalled(p) {
    const pub = published(p.id);
    const older = pub && cmpVersion(pub.version, p.version) < 0 ? pub : null;
    const reqs = p.requires || [];
    const actions = h("div", { class: "actions" },
      p.status === "off"
        ? h("button", { class: "btn primary", type: "button", onclick: () => simple("enable", p.id) }, t("pluginmgr_enable"))
        : h("button", { class: "btn", type: "button", onclick: () => simple("disable", p.id) }, t("pluginmgr_disable")),
      h("button", { class: "btn", type: "button", disabled: p.status === "off", onclick: () => simple("reload", p.id, t("pluginmgr_reloaded")) }, t("pluginmgr_reload")),
      p.removable ? h("button", { class: "btn danger", type: "button", style: "margin-left:auto", onclick: () => uninstall(p) }, icon("trash"), t("pluginmgr_uninstall")) : null);
    detailHost.append(panel(p.name + " · v" + p.version,
      h("div", { class: "pl-head" },
        h("span", { class: "dot " + dotOf(p.status), "aria-hidden": "true" }), h("span", null, statusText(p)), typeBadges(p.types)),
      p.update && pub ? h("div", { class: "msg upd" }, h("span", null, t("pluginmgr_update_to", { ver: p.update })),
        h("button", { class: "btn primary", type: "button", disabled: S.busy, onclick: () => install(pub.url) }, t("pluginmgr_update_btn"))) : null,
      older ? h("div", { class: "msg warn" }, h("span", null, t("pluginmgr_newer_installed", { have: p.version, pub: older.version })),
        h("button", { class: "btn", type: "button", disabled: S.busy, onclick: () => install(older.url) }, t("pluginmgr_install_published", { ver: older.version }))) : null,
      p.panel ? h("div", { class: "msg note" }, t("set_plugin_panel_only")) : null,
      p.description ? h("p", { class: "pl-desc" }, p.description) : null,
      h("dl", { class: "kv" },
        p.author ? [h("dt", null, t("set_plugin_author")), h("dd", null, p.author)] : null,
        p.path ? [h("dt", null, t("set_plugin_location")), h("dd", { class: "mono" }, p.path)] : null,
        reqs.length ? [h("dt", null, t("set_plugin_requires")), h("dd", null, reqs.map((r) =>
          h("div", { class: r.ok ? "" : "bad" }, t(r.ok ? "pluginmgr_requires_ok" : "pluginmgr_requires_missing", { pkg: r.name }))))] : null),
      p.status === "bad" && p.error ? h("div", { class: "msg err", role: "alert" }, p.error) : null,
      msgBox(),
      actions));
  }

  function renderAvail(p) {
    detailHost.append(panel(p.name + " · v" + p.version,
      h("div", { class: "pl-head" }, typeBadges(p.type || p.types)),
      p.description ? h("p", { class: "pl-desc" }, p.description) : null,
      h("dl", { class: "kv" },
        p.author ? [h("dt", null, t("set_plugin_author")), h("dd", null, p.author)] : null,
        p.url ? [h("dt", null, "URL"), h("dd", { class: "mono" }, p.url)] : null),
      msgBox(),
      h("div", { class: "actions" },
        h("button", { class: "btn primary", type: "button", disabled: S.busy || !p.url, onclick: () => install(p.url) },
          S.busy ? t("pluginmgr_installing") : t("pluginmgr_install_btn")))));
  }

  function renderStatus() {
    clear(statusHost);
    if (S.indexState === "error") statusHost.append(h("span", { class: "chip warn" }, t("pluginmgr_fetch_fail", { err: S.indexErr })));
    const n = updatable().length;
    if (n) statusHost.append(h("span", { class: "chip" }, n === 1 ? t("plugin_updates_one") : t("plugin_updates_many", { n })));
  }

  function renderAll() {
    renderList();
    renderStatus();
    clear(detailHost);
    if (S.sel.kind === "installed" && byId(S.sel.id)) renderInstalled(byId(S.sel.id));
    else if (S.sel.kind === "avail" && published(S.sel.id) && !byId(S.sel.id)) renderAvail(published(S.sel.id));
    else { S.sel = { kind: "install" }; renderInstall(); }
  }

  scope.on("plugins.changed", () => loadInstalled());
  scope.on("plugins.updates", () => loadInstalled());

  renderAll();
  await loadInstalled();
  if (scope.alive) loadIndex();
}

export function unmount() { if (scope) scope.dispose(); scope = null; }
