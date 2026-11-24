// The Profile page of a device (Everest Max, Makalu, DisplayPad): the list of software profiles on the
// left, the active one on the right. The core (core/profiles.py, service "profiles") does the work; every
// command takes the device. Also the rail entry ("Profile" first in every device's rail) and the toasts.
import bc from "./bridge.js";
import { t } from "./i18n.js";
import { h, icon, clear, toast, confirmDialog, makeScope } from "./ui.js";
import { openImageEditor } from "./image-editor.js";

const ERR_CODES = ["unknown_device", "no_adapter", "not_found", "limit", "last_profile", "bad_name", "bad_picture",
  "bad_file", "wrong_device", "no_data", "snapshot_failed", "busy"];

export function errMsg(r) {
  const code = r && r.code;
  if (code && ERR_CODES.includes(code)) return t("prof_err_" + code, (r && r.params) || {});
  return t("ui_error", { err: (r && r.error) || "?" });
}

const baseName = (p) => String(p || "").split(/[\\/]/).pop();

/** Calls profiles.<cmd>; a refusal becomes a toast and null. */
async function call(cmd, params) {
  const r = await bc.call("profiles." + cmd, params);
  if (!r.ok) { toast(errMsg(r), "err"); return null; }
  return r;
}

/** The toast text for "<name> is now the active ... profile". */
export function activatedText(device, name) {
  const key = device === "makalu" ? "prof_active_mouse" : device === "displaypad" ? "prof_active_pad" : "prof_active_kbd";
  return t(key, { name });
}

// ── the rail entry ─────────────────────────────────────────────────────────

/** The "Profile" link for a device's rail: the active profile's picture, or its number in a tile. */
export function railItem(device, active) {
  const tile = h("span", { class: "prof-rail-tile" }, "1");
  const a = h("a", { href: "#/" + device + "/profile", class: active === "profile" ? "on" : "", "aria-current": active === "profile" ? "page" : null },
    tile, t("prof_rail"));
  const draw = async () => {
    const r = await bc.call("profiles.list", { device });
    if (!r.ok || !a.isConnected) return;
    const list = r.profiles || [];
    const i = Math.max(0, list.findIndex((p) => p.id === r.active));
    const p = list[i];
    const url = p && p.picture_url ? await bc.image(p.picture_url, 64) : null;
    clear(tile);
    if (url) tile.append(h("img", { src: url, alt: "" })); else tile.textContent = String(i + 1);
  };
  draw();
  const off = bc.on("profiles.*", (_t, p) => {
    if (!a.isConnected) { off(); return; }
    if (!p || !p.device || p.device === device) draw();
  });
  return a;
}

// ── global toasts (started once from app.js) ───────────────────────────────

export function startToasts() {
  bc.on("profiles.activated", (_t, p) => { if (p && p.name) toast(activatedText(p.device, p.name), "ok"); });
  bc.on("profiles.migrated", (_t, p) => {
    const created = (p && p.created) || [];
    const sk = p && p.skipped;
    const skipped = Array.isArray(sk) ? sk.length : Number(sk || 0);
    if (created.length) toast(t("prof_migrated", { n: created.length }), "ok");
    if (skipped) toast(t("prof_migrated_skipped", { n: skipped }), "ok", { ms: 12000 });
  });
}

// ── the page ───────────────────────────────────────────────────────────────

/** Fills `col` (the list) and `main` (the active profile) of a device frame. Returns the scope to dispose. */
export async function mountProfiles(col, main, device) {
  const scope = makeScope();
  const S = { profiles: [], active: "", limit: null, urls: new Map(), editing: false, jobs: new Map(), sent: 0, queued: 0 };

  const tile = (p, i, size) => {
    const el = h("div", { class: "prof-tile" }, h("span", { class: "n" }, String(i + 1)));
    if (p.picture_url) {
      const key = p.picture_url + "@" + size;
      const put = (url) => { if (url && el.isConnected) { clear(el); el.append(h("img", { src: url, alt: "" })); } };
      if (!S.urls.has(key)) S.urls.set(key, bc.image(p.picture_url, size));
      S.urls.get(key).then(put);
    }
    return el;
  };

  // ── the left column ─────────────────────────────────────────────────────
  const count = h("span", { class: "prof-count" });
  const importBtn = h("button", { class: "iconbtn", type: "button", title: t("prof_import"), "aria-label": t("prof_import"), onclick: doImport }, icon("down"));
  const exportBtn = h("button", { class: "iconbtn", type: "button", title: t("prof_export"), "aria-label": t("prof_export"), onclick: doExport }, icon("up"));
  const newBtn = h("button", { class: "newbtn", type: "button", onclick: doCreate }, icon("plus"), t("prof_new"));
  const rows = h("div", { class: "rows prof-rows" });
  col.classList.add("prof-col");
  col.append(
    h("div", { class: "head" }, h("span", { class: "acc" }), h("h2", { class: "t h", style: "margin:0;font-weight:400" }, t("prof_list_title"), " ", count),
      h("span", { style: "margin-left:auto;display:flex;gap:2px" }, importBtn, exportBtn)),
    newBtn, rows);

  // ── the right panel ─────────────────────────────────────────────────────
  const panel = h("div", { class: "prof-panel" });
  main.append(panel);

  const active = () => S.profiles.find((p) => p.id === S.active) || S.profiles[0] || null;

  function drawList() {
    clear(rows);
    S.profiles.forEach((p, i) => {
      const on = p.id === S.active;
      rows.append(h("div", { class: "row" + (on ? " on" : "") },
        h("button", { class: "pick", type: "button", "aria-pressed": on ? "true" : "false", onclick: () => activate(p) },
          tile(p, i, 96),
          h("div", { class: "txt" }, h("div", { class: "pos" }, String(i + 1)), h("div", { class: "nm h" }, p.name))),
        h("div", { class: "acts" }, h("button", { class: "iconbtn", type: "button", title: t("prof_delete") + ": " + p.name,
          "aria-label": t("prof_delete") + ": " + p.name, disabled: S.profiles.length <= 1, onclick: () => doDelete(p) }, icon("trash")))));
    });
    count.textContent = S.profiles.length + (S.limit ? "/" + S.limit : "");
    newBtn.disabled = !!(S.limit && S.profiles.length >= S.limit);
    exportBtn.disabled = !active();
  }

  const progress = h("div", { class: "prof-progress", role: "status", hidden: true });
  function drawProgress() {
    clear(progress);
    const jobs = [...S.jobs.values()];
    progress.hidden = !jobs.length;
    if (!jobs.length) return;
    const j = jobs[0];
    const what = j.kind === "main_upload" ? t("prof_main_picture") : "D" + ((j.button != null ? j.button : 0) + 1);
    progress.append(h("span", null, t("prof_sending", { what, n: Math.min(S.sent + 1, S.queued), total: S.queued })),
      h("div", { class: "pbar" + (typeof j.pct === "number" ? "" : " busy"), role: "progressbar" },
        h("i", { style: typeof j.pct === "number" ? "width:" + Math.max(2, j.pct) + "%" : null })));
  }

  function drawPanel() {
    clear(panel);
    const p = active();
    if (!p) return;
    const i = S.profiles.indexOf(p);

    const pic = h("button", { class: "prof-pic", type: "button", title: t("prof_picture_change"), "aria-label": t("prof_picture_change"), onclick: () => changePicture(p) },
      tile(p, i, 280), h("span", { class: "pen" }, icon("pencil")));
    const picWrap = h("div", { class: "prof-picwrap" }, pic,
      p.picture_url ? h("button", { class: "iconbtn prof-picx", type: "button", title: t("prof_picture_remove"), "aria-label": t("prof_picture_remove"), onclick: () => removePicture(p) }, "×") : null);

    const nameRow = h("div", { class: "prof-namerow" });
    const drawName = () => {
      clear(nameRow);
      if (S.editing) {
        const inp = h("input", { class: "field prof-nameinp", type: "text", value: p.name, maxlength: 40, "aria-label": t("prof_rename") });
        let done = false;
        const finish = async (save) => {
          if (done) return;
          done = true; S.editing = false;
          const v = inp.value.trim();
          if (save && v && v !== p.name) { const r = await call("rename", { device, id: p.id, name: v }); if (r) await load(); else drawName(); }
          else drawName();
        };
        inp.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); finish(true); } else if (e.key === "Escape") finish(false); });
        inp.addEventListener("blur", () => finish(true));
        nameRow.append(inp);
        inp.focus(); inp.select();
      } else {
        nameRow.append(h("h2", { class: "prof-name h" }, p.name),
          h("button", { class: "iconbtn", type: "button", title: t("prof_rename"), "aria-label": t("prof_rename"), onclick: () => { S.editing = true; drawName(); } }, icon("pencil")));
      }
    };
    drawName();

    const prog = h("input", { class: "field", type: "text", readonly: true, id: "prof-program", value: p.program || "", placeholder: t("prof_program_none"), title: p.program || "" });
    const clearBtn = p.program ? h("button", { class: "iconbtn prof-clear", type: "button", title: t("prof_program_clear"), "aria-label": t("prof_program_clear"), onclick: async () => {
      if (await call("set_program", { device, id: p.id, path: null })) load();
    } }, "×") : null;
    const browse = h("button", { class: "btn", type: "button", onclick: async () => {
      const path = await bc.pickFile({ title: t("prof_program_label"), kind: "any" });
      if (path && await call("set_program", { device, id: p.id, path })) load();
    } }, t("prof_browse"));

    panel.append(
      h("div", { class: "prof-top" }, picWrap,
        h("div", { class: "prof-who" }, h("div", { class: "lab", style: "margin:0 0 6px" }, t("prof_active_label")), nameRow)),
      progress,
      h("hr", { class: "prof-hr" }),
      h("label", { class: "lab", for: "prof-program" }, t("prof_program_label")),
      h("div", { class: "prof-prog" }, h("div", { class: "prof-proginp" }, prog, clearBtn), browse),
      h("div", { class: "prof-btns" },
        h("button", { class: "btn prof-wide", type: "button", onclick: () => doDuplicate(p) }, icon("copy"), t("prof_duplicate")),
        h("button", { class: "btn prof-wide", type: "button", onclick: () => doReset(p) }, icon("refresh"), t("prof_reset"))));
    drawProgress();
  }

  async function load() {
    const r = await bc.call("profiles.list", { device });
    if (!scope.alive) return;
    if (!r.ok) { toast(errMsg(r), "err"); return; }
    S.profiles = r.profiles || [];
    S.active = r.active || (S.profiles[0] && S.profiles[0].id) || "";
    S.limit = r.limit || null;
    S.urls.clear();
    drawList();
    if (!S.editing) drawPanel();
  }

  // ── actions ─────────────────────────────────────────────────────────────
  async function activate(p) {
    if (p.id === S.active) return;
    if (await call("activate", { device, id: p.id })) load();
  }
  async function doCreate() { if (await call("create", { device })) { S.editing = false; await load(); } }
  async function doDuplicate(p) { if (await call("duplicate", { device, id: p.id })) { S.editing = false; await load(); } }
  async function doDelete(p) {
    const ok = await confirmDialog({ title: t("prof_delete"), message: t("prof_delete_confirm", { name: p.name }), okLabel: t("prof_delete"), danger: true });
    if (ok && await call("delete", { device, id: p.id })) load();
  }
  async function doReset(p) {
    const ok = await confirmDialog({ title: t("prof_reset"), message: t("prof_reset_confirm", { name: p.name }), okLabel: t("prof_reset_ok"), danger: true });
    if (ok && await call("reset", { device, id: p.id })) { toast(t("prof_reset_done", { name: p.name }), "ok"); load(); }
  }
  async function changePicture(p) {
    const path = await openImageEditor({ target: "everest_dkey", current: p.picture_url || null, title: t("prof_picture_change") });
    if (path && await call("set_picture", { device, id: p.id, path })) load();
  }
  async function removePicture(p) { if (await call("set_picture", { device, id: p.id, path: null })) load(); }
  async function doExport() {
    const p = active();
    if (!p) return;
    const path = await bc.pickFile({ title: t("prof_export"), kind: "save", name: p.name + ".bcprofile" });
    if (!path) return;
    const r = await call("export", { device, id: p.id, path });
    if (r) toast(t("prof_exported", { file: baseName(r.path || path) }), "ok");
  }
  async function doImport() {
    const path = await bc.pickFile({ title: t("prof_import"), kind: "any" });
    if (!path) return;
    const r = await call("import", { path, device });
    if (!r) return;
    toast(t("prof_imported", { name: baseName(path).replace(/\.bcprofile$/i, "") }), "ok");
    load();
  }

  // ── events ──────────────────────────────────────────────────────────────
  scope.on("profiles.*", (topic, p) => {
    if (topic === "profiles.migrated" || (p && p.device && p.device !== device)) return;
    load();
  });
  if (device === "everest_max") {
    const isPic = (k) => k === "dbutton_upload" || k === "main_upload";
    scope.on("everest_max.*", (topic, p) => {
      p = p || {};
      if (!isPic(p.kind)) return;
      switch (topic.slice("everest_max.".length)) {
        case "job_started": S.jobs.set(p.id, { ...p, pct: null }); S.queued += 1; break;
        case "job_progress": { const j = S.jobs.get(p.id) || { ...p }; if (typeof p.pct === "number") j.pct = p.pct; S.jobs.set(p.id, j); break; }
        case "job_done": S.jobs.delete(p.id); S.sent += 1; if (!S.jobs.size) { S.sent = 0; S.queued = 0; } break;
        default: return;
      }
      drawProgress();
    });
    bc.call("everest_max.status").then((r) => {
      if (!scope.alive || !r.ok) return;
      for (const j of r.jobs || []) if (isPic(j.kind) && !S.jobs.has(j.id)) { S.jobs.set(j.id, j); S.queued += 1; }
      drawProgress();
    });
  }

  await load();
  return scope;
}
