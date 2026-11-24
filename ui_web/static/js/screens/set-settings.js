// Settings: language, start-up, profiles, backup and restore, updates, device photos, version.
import bc from "../bridge.js";
import { t, languages } from "../i18n.js";
import { h, append, clear, toast, makeScope, confirmDialog } from "../ui.js";

const put = (host, ...kids) => append(host, kids);
import { frame, panel, errText } from "./set-common.js";
import { errMsg } from "../profiles-page.js";
import { openHistory } from "../update-history.js";

let scope = null;
let lastTab = "general";              // the tab shown last, kept while the app runs

const UPDATE_HINTS = { arch: "settings_update_hint_arch", debian: "settings_update_hint_debian", source: "settings_update_hint_source", appimage: "settings_update_hint_appimage" };

function updateError(code, message) {
  switch (code) {
    case "no_asset": return t("settings_update_no_asset");
    case "no_checksum": return t("settings_update_no_checksum");
    case "bad_checksum": return t("settings_update_bad_checksum");
    default: return t("settings_update_error", { err: message || code || "?" });
  }
}

function progressLine(o) {
  return h("div", { class: "updline", role: "status" }, t("set_stage_" + o.stage, { pct: o.pct }),
    h("div", { class: "progress", role: "progressbar", "aria-valuemin": 0, "aria-valuemax": 100, "aria-valuenow": o.pct }, h("i", { style: "width:" + o.pct + "%" })));
}

function photoError(r) {
  switch (r.code) {
    case "network": return t("set_dl_err_network");
    case "bad_package": return t("set_dl_err_bad_package");
    case "not_found": return t("set_photos_err_not_found");
    case "no_images": return t("set_photos_err_no_images");
    default: return errText(r);
  }
}

export async function mount(el) {
  scope = makeScope();
  const fr = frame(el, "settings", false);
  // Two tabs with two columns each (app.css .tabbar, .cols2): the eight panels under each other were
  // more than twice the window's height.
  const sections = h("div", { style: "width:100%" });
  fr.main.append(sections);

  const check = (label, checked, fn) => {
    const box = h("input", { type: "checkbox", checked });
    box.addEventListener("change", () => fn(box.checked, box));
    return h("label", { class: "check" }, box, h("span", null, label));
  };

  // ── language ───────────────────────────────────────────────────────────────
  const langHost = h("div");
  async function drawLanguage() {
    const { list, current } = await languages();
    if (!scope.alive) return;
    const sel = h("select", { class: "field", id: "set-lang", value: current },
      Object.entries(list).map(([code, name]) => h("option", { value: code }, name)));
    sel.value = current;
    sel.addEventListener("change", async () => {
      const r = await bc.call("i18n.set_language", { code: sel.value });
      if (!r.ok) toast(errText(r), "err");
    });
    clear(langHost);
    langHost.append(h("label", { class: "lab", for: "set-lang" }, t("settings_language")), sel);
  }

  // ── start-up ───────────────────────────────────────────────────────────────
  const appHost = h("div", { class: "checks", id: "settings-app-row" });
  function drawApp(s) {
    clear(appHost);
    appHost.append(
      check(t("settings_autostart"), !!s.autostart, async (v, box) => {
        const r = await bc.call("settings.set_autostart", { enabled: v });
        if (!r.ok) { box.checked = !v; toast(errText(r), "err"); } else toast(t("settings_saved"), "ok");
      }));   // no splash option: this interface has no splash screen (see ui_web/app.py)
    // The classic interface of 3.x: chosen here, back again in its own settings.
    // On the autostart line: a row of its own would make the page scroll at 1100x700.
    appHost.append(h("button", { class: "btn uiswitch", type: "button", title: t("settings_ui_classic_hint"),
      onclick: toClassic }, t("settings_ui_classic_btn")));
  }
  async function toClassic() {
    const ok = await confirmDialog({ title: t("settings_ui_classic"), message: t("settings_ui_classic_confirm"),
      okLabel: t("settings_ui_restart") });
    if (!ok) return;
    const r = await bc.call("settings.set_interface", { interface: "classic" });
    if (!r.ok) { toast(errText(r), "err"); return; }
    const rr = await bc.call("shell.restart");
    if (rr && rr.ok === false) toast(errText(rr), "err");
  }

  // ── profiles: they live on each device's page now; left-over 3.x folders can still be imported ──
  const profHost = h("div");
  async function import3x() {
    const path = await bc.pickFile({ title: t("settings_import3_title"), kind: "folder" });
    if (!path) return;
    const r = await bc.call("profiles.import", { path });
    if (!r.ok) return toast(errMsg(r), "err");
    const names = (r.profiles || []).map((p) => p.name || p.id);
    toast(names.length ? t("settings_import3_done", { n: names.length, names: names.join(", ") }) : t("settings_import3_none"), names.length ? "ok" : "err");
  }
  profHost.append(h("p", { class: "help", style: "margin:0 0 14px" }, t("settings_profiles_hint")),
    h("button", { class: "btn", type: "button", onclick: import3x }, t("settings_import3")));

  // ── backup ─────────────────────────────────────────────────────────────────
  async function backup() {
    const path = await bc.pickFile({ title: t("settings_backup"), kind: "save", name: "basecamp-backup.zip" });
    if (!path) return;
    const r = await bc.call("settings.export_backup", { path });
    toast(r.ok ? t("settings_backup_ok", { n: r.count }) : t("settings_backup_err", { err: r.error }), r.ok ? "ok" : "err");
  }
  async function restore() {
    const path = await bc.pickFile({ title: t("settings_restore"), kind: "any" });
    if (!path) return;
    const ok = await confirmDialog({ title: t("settings_restore"), message: t("settings_restore_confirm"), okLabel: t("settings_restore").replace(/…$/, ""), danger: true });
    if (!ok) return;
    const r = await bc.call("settings.import_backup", { path });
    toast(r.ok ? t("settings_restore_ok", { n: r.count }) : t("settings_restore_err", { err: r.error }), r.ok ? "ok" : "err");
  }

  // ── updates ────────────────────────────────────────────────────────────────
  const U = { version: "", type: "source", canInstall: false, info: null, checked: false, checking: false, state: "idle", pct: 0, error: "" };
  const updHost = h("div");
  function drawUpdates() {
    clear(updHost);
    const line = (kids) => updHost.append(h("div", { class: "updline", role: "status" }, kids));
    if (U.state === "downloading") {
      line([t("settings_update_downloading", { pct: U.pct }),
        h("div", { class: "progress", role: "progressbar", "aria-valuemin": 0, "aria-valuemax": 100, "aria-valuenow": U.pct }, h("i", { style: "width:" + U.pct + "%" }))]);
    } else if (U.state === "installing") line(t("settings_update_installing"));
    else if (U.state === "done") {
      line(t("settings_update_done"));
      updHost.append(h("div", { class: "actions" }, h("button", { class: "btn primary", type: "button", onclick: restart }, t("settings_update_restart"))));
    } else {
      if (U.state === "error") updHost.append(h("div", { class: "msg err", role: "alert" }, updateError(U.errCode, U.error)));
      if (U.info) {
        line([t("settings_update_available", { ver: U.info.version }), " ",
          h("button", { class: "linkbtn", type: "button", onclick: () => openHistory({ focus: U.info.version }) }, t("upd_whats_new"))]);
        if (U.canInstall && U.info.url) updHost.append(h("div", { class: "actions" },
          h("button", { class: "btn primary", type: "button", onclick: download }, t("settings_update_button"))));
        else updHost.append(h("div", { class: "help" }, t(UPDATE_HINTS[U.type] || UPDATE_HINTS.source)));
      } else if (U.checked) line(t("settings_update_current", { ver: U.version }));
      updHost.append(h("div", { class: "actions", style: "margin-top:12px" },
        h("button", { class: "btn", type: "button", disabled: U.checking, onclick: checkNow }, U.checking ? t("pluginmgr_loading") : t("set_update_check")),
        h("button", { class: "btn", type: "button", onclick: () => openHistory() }, t("upd_history_btn"))));
    }
  }
  async function checkNow() {
    U.checking = true; U.state = "idle"; drawUpdates();
    const r = await bc.call("update.check");
    U.checking = false;
    if (!scope.alive) return;
    U.checked = true;
    U.info = r.ok && r.update ? r.update : null;
    drawUpdates();
  }
  async function download() {
    U.state = "downloading"; U.pct = 0; drawUpdates();
    const r = await bc.call("update.download");
    if (!r.ok) { U.state = "error"; U.errCode = "failed"; U.error = r.error; drawUpdates(); }
  }
  async function restart() {
    const r = await bc.call("update.restart");
    if (!r.ok) toast(errText(r), "err");
  }
  scope.on("update.available", (_t, p) => { if (p && p.version) { U.info = p; U.checked = true; if (U.state === "idle") drawUpdates(); } });
  scope.on("update.progress", (_t, p) => { U.state = "downloading"; U.pct = (p || {}).pct || 0; drawUpdates(); });
  scope.on("update.installing", () => { U.state = "installing"; drawUpdates(); });
  scope.on("update.done", () => { U.state = "done"; drawUpdates(); });
  scope.on("update.error", (_t, p) => { U.state = "error"; U.errCode = (p || {}).code; U.error = (p || {}).message; drawUpdates(); });

  // ── device photos ──────────────────────────────────────────────────────────
  const photoHost = h("div");
  const P = { imported: false, source: "", found: false, path: "", busy: false, error: "", stage: "", pct: 0 };
  function drawPhotos() {
    clear(photoHost);
    put(photoHost, h("p", { class: "help", style: "margin:14px 0" }, t("set_photos_explain")),
      h("div", { class: "updline", role: "status" }, P.imported ? t("set_photos_imported", { source: P.source || "?" }) : t("set_photos_none")),
      P.found && !P.imported ? h("div", { class: "help" }, t("set_photos_found", { path: P.path })) : null,
      P.busy && P.stage ? progressLine(P) : null,
      P.error ? h("div", { class: "msg err", role: "alert" }, P.error) : null,
      h("div", { class: "actions", style: "flex-wrap:wrap" },
        h("button", { class: "btn", type: "button", disabled: P.busy, onclick: downloadPhotos }, t("set_photos_download")),
        h("button", { class: "btn", type: "button", disabled: P.busy, onclick: async () => {
          const path = await bc.pickFile({ title: t("set_photos_import_file"), kind: "any" });
          if (path) importPhotos(path, true);
        } }, t("set_photos_import_file")),
        h("button", { class: "btn primary", type: "button", disabled: P.busy || !P.found, onclick: () => importPhotos(null) },
          P.busy ? t("pluginmgr_installing") : t("set_photos_import_found")),
        h("button", { class: "btn", type: "button", disabled: P.busy, onclick: async () => {
          const path = await bc.pickFile({ title: t("set_photos_pick"), kind: "folder" });
          if (path) importPhotos(path);
        } }, t("set_photos_import_folder")),
        P.imported ? h("button", { class: "btn danger", type: "button", disabled: P.busy, onclick: removePhotos }, t("set_photos_remove")) : null),
      // The hint on the drawings (photo-hint.js) was hidden: it can come back while there are no photos.
      !P.imported && P.hintHidden ? h("button", { class: "btn", type: "button", style: "margin-top:10px",
        onclick: () => bc.call("device_images.set_hint_hidden", { hidden: false }) }, t("photo_hint_show")) : null);
  }
  async function loadPhotos() {
    const [st, found] = await Promise.all([bc.call("device_images.status"), bc.call("device_images.find")]);
    if (!scope.alive) return;
    P.imported = !!(st.ok && st.imported);
    P.source = (st.ok && st.source) || "";
    P.hintHidden = !!(st.ok && st.hint_hidden);
    P.found = !!(found.ok && found.found);
    P.path = (found.ok && found.path) || "";
    drawPhotos();
  }
  async function downloadPhotos() {
    const ok = await confirmDialog({ title: t("set_photos_download"), message: t("set_photos_download_confirm"), okLabel: t("set_photos_download") });
    if (!ok) return;
    P.busy = true; P.error = ""; P.stage = "download"; P.pct = 0; drawPhotos();
    const r = await bc.call("device_images.download", {});
    if (!scope.alive) return;
    if (!r.ok) { P.busy = false; P.stage = ""; P.error = photoError(r); drawPhotos(); }
    // otherwise progress and the result arrive as events
  }
  scope.on("device_images.progress", (_t, p) => { p = p || {}; P.busy = true; P.stage = p.stage || "download"; P.pct = p.pct || 0; drawPhotos(); });
  scope.on("device_images.changed", () => { P.busy = false; P.stage = ""; P.error = ""; loadPhotos(); });
  scope.on("device_images.error", (_t, p) => { P.busy = false; P.stage = ""; P.error = photoError(p || {}); drawPhotos(); });
  async function importPhotos(path, file) {
    P.busy = true; P.error = ""; drawPhotos();
    const r = file ? await bc.call("device_images.import_file", { path }) : await bc.call("device_images.import", path ? { path } : {});
    P.busy = false;
    if (!scope.alive) return;
    if (!r.ok) { P.error = photoError(r); drawPhotos(); return; }
    toast(t("set_photos_done"), "ok");
    loadPhotos();
  }
  async function removePhotos() {
    const r = await bc.call("device_images.remove");
    if (!r.ok) return toast(errText(r), "err");
    P.error = "";
    loadPhotos();
  }

  // ── icon packs ─────────────────────────────────────────────────────────────
  const packHost = h("div");
  const K = { packs: [], available: [], busy: false, stage: "", pct: 0, error: "", missing: false };
  function packInfo(name) {
    const list = Array.isArray(K.packs) ? K.packs : Object.entries(K.packs || {}).map(([id, v]) => ({ id, ...(v || {}) }));
    return list.find((p) => (p.id || p.pack || p.name) === name) || null;
  }
  function packError(r) {
    switch (r.code) {
      case "network": return t("set_dl_err_network");
      case "not_found": return t("set_dl_err_not_found");
      case "bad_package": return t("set_dl_err_bad_package");
      default: return r.message || r.error ? t("ui_error", { err: r.message || r.error }) : "?";
    }
  }
  function basecampInfo() {
    const list = Array.isArray(K.packs) ? K.packs : [];
    const own = list.filter((p) => String(p.id || "").startsWith("basecamp-"));
    const av = (K.available || []).find((a) => a.id === "basecamp");
    if (!own.length && !(av && av.installed)) return null;
    return { count: own.reduce((n, p) => n + (p.count || 0), 0) };
  }
  function drawPacks() {
    clear(packHost);
    const info = packInfo("mountain");
    const count = info ? (info.count != null ? info.count : "") : "";
    const bci = basecampInfo();
    put(packHost, h("p", { class: "help", style: "margin:14px 0" }, t("set_packs_explain")),
      h("div", { class: "updline", role: "status" }, h("strong", null, t("set_pack_mountain")), " · ",
        info ? t("set_pack_installed", { n: count }) : t("set_pack_not_installed")),
      info && info.folder ? h("div", { class: "help mono" }, info.folder) : null,
      h("div", { class: "actions", style: "flex-wrap:wrap" },
        h("button", { class: "btn primary", type: "button", disabled: K.busy, onclick: () => installPack("mountain") }, info ? t("set_pack_reinstall") : t("set_pack_install")),
        h("button", { class: "btn", type: "button", disabled: K.busy, onclick: async () => {
          const path = await bc.pickFile({ title: t("set_pack_from_zip"), kind: "any" });
          if (path) installPack(path);
        } }, t("set_pack_from_zip")),
        info ? h("button", { class: "btn danger", type: "button", disabled: K.busy, onclick: () => removePack("mountain") }, t("set_pack_remove")) : null),
      h("div", { class: "updline", role: "status", style: "margin-top:18px" }, h("strong", null, t("set_pack_basecamp")), " · ",
        bci ? t("set_pack_installed", { n: bci.count }) : t("set_pack_not_installed")),
      h("div", { class: "help" }, t("set_pack_basecamp_help")),
      h("div", { class: "actions", style: "flex-wrap:wrap" },
        h("button", { class: "btn primary", type: "button", disabled: K.busy, onclick: () => installPack("basecamp") }, bci ? t("set_pack_reinstall") : t("set_pack_install")),
        h("button", { class: "btn", type: "button", disabled: K.busy, onclick: async () => {
          const path = await bc.pickFile({ title: t("set_pack_from_file"), kind: "any" });
          if (path) installPack("basecamp", path);
        } }, t("set_pack_from_file")),
        bci ? h("button", { class: "btn danger", type: "button", disabled: K.busy, onclick: () => removePack("basecamp") }, t("set_pack_remove")) : null),
      K.busy && K.stage ? progressLine(K) : null,
      K.error ? h("div", { class: "msg err", role: "alert" }, K.error) : null);
  }
  async function loadPacks() {
    const r = await bc.call("icon_packs.status");
    if (!scope.alive) return;
    K.packs = r.ok ? (r.packs || r.installed || []) : [];
    K.available = r.ok ? (r.available || []) : [];
    K.missing = !r.ok;
    if (!r.ok) K.error = r.error || "";
    drawPacks();
  }
  async function installPack(pack, path) {
    K.busy = true; K.error = ""; K.stage = "download"; K.pct = 0; drawPacks();
    const r = await bc.call("icon_packs.install", path ? { pack, path } : { pack });
    if (!scope.alive) return;
    if (!r.ok) { K.busy = false; K.stage = ""; K.error = packError(r); drawPacks(); }
  }
  async function removePack(pack) {
    const r = await bc.call("icon_packs.remove", { pack });
    if (!r.ok) { K.error = packError(r); drawPacks(); return; }
    K.error = "";
    loadPacks();
  }
  scope.on("icon_packs.progress", (_t, p) => { p = p || {}; K.busy = true; K.stage = p.stage || "download"; K.pct = p.pct || 0; drawPacks(); });
  scope.on("icon_packs.changed", () => { K.busy = false; K.stage = ""; K.error = ""; loadPacks(); });
  scope.on("icon_packs.error", (_t, p) => { K.busy = false; K.stage = ""; K.error = packError(p || {}); drawPacks(); });

  // ── the page ───────────────────────────────────────────────────────────────
  const aboutHost = h("div");
  const general = h("div", { class: "cols2", "data-tabpanel": "general" },
    h("div", { class: "stack" },
      panel(t("settings_language"), h("div", { style: "margin-top:20px;max-width:360px" }, langHost)),
      panel(t("settings_app_section"), h("div", { style: "margin-top:16px" }, appHost)),
      panel(t("settings_profiles"), h("div", { style: "margin-top:16px" }, profHost))),
    h("div", { class: "stack" },
      panel(t("settings_backup_section"),
        h("p", { class: "help", style: "margin:14px 0" }, t("settings_backup_hint")),
        h("div", { class: "actions", style: "margin-top:0" },
          h("button", { class: "btn", type: "button", onclick: backup }, t("settings_backup")),
          h("button", { class: "btn", type: "button", onclick: restore }, t("settings_restore")))),
      h("div", { id: "settings-update" }, panel(t("settings_update_section"), h("div", { style: "margin-top:16px" }, updHost))),
      panel(t("settings_about_section"), aboutHost)));
  const media = h("div", { class: "cols2", "data-tabpanel": "media" },
    h("div", { class: "stack" }, h("div", { id: "settings-photos" }, panel(t("set_photos_title"), photoHost))),
    h("div", { class: "stack" }, panel(t("set_packs_title"), packHost)));
  const TABS = [["general", t("set_rail_general"), general], ["media", t("set_tab_media"), media]];
  const tabBar = h("div", { class: "tabbar", role: "tablist", "aria-label": t("set_rail_general") });
  const showTab = (id) => {
    lastTab = id;
    for (const [tid, , el] of TABS) {
      el.hidden = tid !== id;
      const b = tabBar.querySelector(`[data-tab="${tid}"]`);
      b.classList.toggle("on", tid === id);
      b.setAttribute("aria-selected", tid === id ? "true" : "false");
    }
  };
  for (const [tid, label] of TABS) tabBar.append(h("button", { type: "button", class: "tabbtn", role: "tab", "data-tab": tid, onclick: () => showTab(tid) }, label));
  sections.append(tabBar, general, media);
  showTab(lastTab);       // the gear's update badge shows the general tab itself (app.js focusPending)

  drawApp({});
  drawUpdates();
  drawPhotos();
  drawPacks();
  loadPacks();
  drawLanguage();
  const [s, upd] = await Promise.all([bc.call("settings.get"), bc.call("update.status")]);
  if (!scope.alive) return;
  if (s.ok) { drawApp(s); }
  if (upd.ok) {
    U.version = upd.version || ""; U.type = upd.install_type || "source"; U.canInstall = !!upd.can_install; U.info = upd.update || null;
    U.checked = !!U.info;
    drawUpdates();
  }
  aboutHost.append(h("dl", { class: "kv", style: "margin-top:16px" }, h("dt", null, t("settings_about_version")), h("dd", null, U.version || "?")));
  loadPhotos();
}

export function unmount() { if (scope) scope.dispose(); scope = null; }
