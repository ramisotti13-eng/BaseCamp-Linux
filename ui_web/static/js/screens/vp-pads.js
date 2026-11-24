// DisplayPad, virtual pads: the list of pads (made, renamed and deleted here), the selected pad
// as its grid of keys (a click presses, as on the phone), its pages and its size, and with
// "Edit keys" a key inspector (picture, text, action) beside the DisplayPad's.
//
// A virtual pad shows one page at a time: the page its presses run on (vpads.switch_page), so a
// click always presses the key that is shown. Keys are numbered row * 8 + col (pad-grid.js).
// The action menu is the DisplayPad's without the plugins' types, the clock and PC info: those
// need a live picture per key and come to virtual pads later.
//
// "Open as window" puts a pad into a window of its own (shell.open_pad_window, padwin.html), or
// brings its window to the front. Which pads are in a window: shell.pad_windows at start, then
// the event shell.pad_windows_changed {open}; asked again when the main window gets the focus
// back, in case an event was missed.
import bc from "../bridge.js";
import { t } from "../i18n.js";
import { h, icon, clear, toast, makeScope, promptDialog, confirmDialog } from "../ui.js";
import { frame } from "./dp-common.js";
import { padGrid, GRID_COLS } from "../pad-grid.js";
import { pickFromLibrary } from "../library-picker.js";
import { TEXT_DEFAULT, TEXT_FONTS_FALLBACK, textLayer, textButton } from "../key-text.js";
import { FALLBACK_TYPES, parseTypes, typeLabel, actionForm } from "../key-action-form.js";

const MAX_ROWS = 6;
const MAX_COLS = 8;
const MAX_NAME = 40;
const MAIN_PAGE = 0;
/** Built-in types that draw a live picture on their key: not on a virtual pad yet. */
const LIVE_TYPES = ["clock", "pc_info"];

let scope = null;

/** The action types a virtual pad's key can have. */
export function padTypes(types) {
  return types.filter((x) => !x.plugin && !LIVE_TYPES.includes(x.id));
}

export async function mount(el) {
  // The screen's own scope: a later await still sees it ended after unmount() let go of it.
  const sc = scope = makeScope();
  const fr = frame(el, "pads", true);
  const S = {
    pads: [], sel: null, pad: null,       // pad: the vpads.keys reply of the selected pad
    edit: false, key: null, keyPage: null, draft: null, dirty: false,
    types: padTypes(FALLBACK_TYPES), fonts: TEXT_FONTS_FALLBACK,
    wins: new Set(),                      // the pads in a window of their own
  };
  let grid = null, gridShape = "";
  fr.main.classList.add("vpmain");

  const askDiscard = () => confirmDialog({ title: t("dp_discard_title"), message: t("dp_discard_msg"), okLabel: t("dp_discard_btn") });
  const fail = (r) => { toast(t("ui_error", { err: r.error }), "err"); };
  const keyNumber = (idx, cols) => Math.floor(idx / GRID_COLS) * cols + (idx % GRID_COLS) + 1;
  const inGrid = (idx, p) => idx != null && Math.floor(idx / GRID_COLS) < p.rows && idx % GRID_COLS < p.cols;
  const keyOf = (idx) => (S.pad && S.pad.keys && S.pad.keys[String(idx)]) || { image: "", text: "", text_style: {}, action: { type: "none", action: "" } };
  const pageOf = (ref) => ((S.pad && S.pad.pages) || []).find((p) => p.id === ref || p.name === ref);

  /** A name from a prompt, at most MAX_NAME characters (the core clamps the same). */
  async function askName(opts) {
    const asked = promptDialog(opts);
    const inp = document.getElementById("dlg-input");
    if (inp) inp.maxLength = MAX_NAME;
    const name = await asked;
    return name ? name.slice(0, MAX_NAME) : null;
  }

  // ── data ────────────────────────────────────────────────────────────────
  async function loadPads() {
    const r = await bc.call("vpads.list");
    if (!sc.alive) return;
    if (!r.ok) { fail(r); return; }
    S.pads = Array.isArray(r.pads) ? r.pads : [];
    if (!S.pads.some((p) => p.id === S.sel)) {
      S.sel = S.pads.length ? S.pads[0].id : null;
      S.pad = null; dropKey();
    }
  }

  async function loadPad() {
    if (!S.sel) { S.pad = null; return; }
    const id = S.sel;
    const r = await bc.call("vpads.keys", { pad: id });
    if (!sc.alive || id !== S.sel) return;
    if (!r.ok) { fail(r); S.pad = null; return; }
    S.pad = r;
    if (S.key != null && !inGrid(S.key, r) && !S.dirty) dropKey();
  }

  /** Take `open` (pad ids) as the pads in a window. False when nothing changed. */
  function setWins(open) {
    if (!Array.isArray(open)) return false;
    const next = new Set(open);
    if (next.size === S.wins.size && [...next].every((id) => S.wins.has(id))) return false;
    S.wins = next;
    return true;
  }

  /** Which pads are in a window. False when that did not change (or cannot be asked here). */
  async function loadWins() {
    const r = await bc.call("shell.pad_windows");
    return sc.alive && r.ok && setWins(r.open);
  }

  async function openWindow(id) {
    const r = await bc.call("shell.open_pad_window", { pad: id });
    if (!sc.alive) return;
    if (!r.ok) return fail(r);
    if (await loadWins()) { renderList(); renderBar(); }
  }

  function dropKey() { S.key = null; S.keyPage = null; S.draft = null; S.dirty = false; }

  // ── pads column ─────────────────────────────────────────────────────────
  function renderList() {
    const col = fr.col;
    const keepFocus = col.contains(document.activeElement) ? document.activeElement.getAttribute("data-f") : null;
    clear(col);
    col.append(
      h("div", { class: "head" }, h("span", { class: "acc" }), h("h2", { class: "t h", style: "margin:0;font-weight:400" }, t("vp_rail"))),
      h("button", { class: "newbtn", type: "button", "data-f": "new", onclick: newPad }, icon("plus"), t("vp_new_pad")));
    const rows = h("div", { class: "rows" });
    for (const p of S.pads) {
      const on = p.id === S.sel;
      rows.append(h("div", { class: "row" + (on ? " on" : "") },
        h("button", { class: "pick", type: "button", "data-f": "pick" + p.id, "aria-pressed": on ? "true" : "false", onclick: () => selectPad(p.id) },
          h("div", { class: "tile" }, icon("vpad")),
          h("div", { class: "txt" },
            h("div", { class: "nm h" }, p.name),
            h("div", { class: "sub" }, t("remote_grid", { rows: p.rows, cols: p.cols }),
              S.wins.has(p.id) ? h("span", { class: "winmark", title: t("vp_win_is_open") }, icon("popout"),
                h("span", { class: "sr-only" }, t("vp_win_is_open"))) : null))),
        h("div", { class: "acts" },
          iconBtn("popout", S.wins.has(p.id) ? t("vp_win_show") : t("vp_win_open_pad", { name: p.name }), () => openWindow(p.id)),
          iconBtn("pencil", t("vp_rename_pad", { name: p.name }), () => renamePad(p)),
          iconBtn("trash", t("vp_delete_pad", { name: p.name }), () => deletePad(p)))));
    }
    col.append(rows);
    if (keepFocus) { const f = col.querySelector(`[data-f="${keepFocus}"]`); if (f) f.focus(); }
  }

  function iconBtn(name, label, fn, disabled) {
    return h("button", { class: "iconbtn", type: "button", title: label, "aria-label": label, disabled, onclick: fn }, icon(name));
  }

  async function selectPad(id) {
    if (id === S.sel) return;
    if (S.dirty && !(await askDiscard())) return;
    S.sel = id; dropKey();
    await loadPad();
    renderAll();
  }

  async function newPad() {
    const name = await askName({ title: t("vp_pad_name_title"), label: t("vp_pad_name_prompt") });
    if (!name) return;
    if (S.dirty && !(await askDiscard())) return;
    const r = await bc.call("vpads.create", { name });
    if (!r.ok) return fail(r);
    S.sel = r.pad && r.pad.id; dropKey();
    await loadPads(); await loadPad();
    renderAll();
  }

  async function renamePad(p) {
    const name = await askName({ title: t("vp_rename_pad_title"), label: t("vp_pad_name_prompt"), value: p.name });
    if (!name || name === p.name) return;
    const r = await bc.call("vpads.rename", { pad: p.id, name });
    if (!r.ok) return fail(r);
    await loadPads(); await loadPad(); renderAll();
  }

  async function deletePad(p) {
    if (!(await confirmDialog({ title: t("vp_delete_pad_title"), message: t("vp_delete_pad_msg", { name: p.name }),
      okLabel: t("dp_delete_page_btn"), danger: true }))) return;
    const r = await bc.call("vpads.delete", { pad: p.id });
    if (!r.ok) return fail(r);
    if (S.sel === p.id) { S.sel = null; dropKey(); }
    await loadPads(); await loadPad(); renderAll();
  }

  // ── main: the bar (page, size, edit), the pad, the inspector ─────────────
  const barHost = h("div", { class: "vpbar" });
  const stage = h("div", { class: "vpstage" });
  const hint = h("div", { class: "vphint" });
  const inspHost = h("div", { class: "vpinsp" });
  fr.main.append(barHost, stage, hint, inspHost);

  function renderBar() {
    clear(barHost);
    const p = S.pad;
    if (!p) return;
    const pageSel = h("select", { class: "field pagesel", id: "vp-page", "data-f": "page" },
      p.pages.map((pg) => h("option", { value: String(pg.id) }, pg.name)));
    pageSel.value = String(p.current_page);
    pageSel.addEventListener("change", () => switchPage(Number(pageSel.value)));
    const cur = pageOf(p.current_page) || { id: MAIN_PAGE, name: "" };
    const num = (id, label, max, value, fn) => {
      const s = h("select", { class: "field num", id }, Array.from({ length: max }, (_v, i) => h("option", { value: String(i + 1) }, String(i + 1))));
      s.value = String(value);
      s.addEventListener("change", () => fn(Number(s.value)));
      return h("div", { class: "grp" }, h("label", { class: "lab", for: id }, label), s);
    };
    const inWin = S.wins.has(p.id);
    // The labels hide in a narrow bar (app.css): the name stays as aria-label and tooltip.
    const winLabel = inWin ? t("vp_win_show") : t("vp_win_open");
    const win = h("button", { class: "btn vpwin" + (inWin ? " on" : ""), type: "button", title: winLabel, "aria-label": winLabel,
      onclick: () => openWindow(p.id) }, icon("popout"), h("span", { class: "lbl" }, winLabel));
    const edit = h("button", { class: "btn vpedit" + (S.edit ? " primary" : ""), type: "button", "aria-pressed": S.edit ? "true" : "false",
      "aria-label": t("vp_edit"), onclick: toggleEdit }, icon("pencil"), h("span", { class: "lbl" }, t("vp_edit")));
    barHost.append(
      h("div", { class: "grp" }, h("label", { class: "lab", for: "vp-page" }, t("vp_page")),
        h("div", { class: "inline-row pagerow" }, pageSel,
          iconBtn("plus", t("vp_new_page"), newPage),
          iconBtn("pencil", t("dp_rename_page_btn") + ": " + cur.name, () => renamePage(cur)),
          iconBtn("trash", t("dp_delete_page_btn") + ": " + cur.name, () => deletePage(cur), cur.id === MAIN_PAGE))),
      num("vp-rows", t("vp_rows"), MAX_ROWS, p.rows, (v) => setGrid(v, p.cols)),
      num("vp-cols", t("vp_cols"), MAX_COLS, p.cols, (v) => setGrid(p.rows, v)),
      win, edit);
  }

  async function switchPage(page) {
    if (!S.pad || page === S.pad.current_page) return;
    if (S.dirty && !(await askDiscard())) { renderBar(); return; }
    dropKey();
    const r = await bc.call("vpads.switch_page", { pad: S.sel, page });
    if (!r.ok) fail(r);
    await loadPad(); renderMain();
  }

  async function newPage() {
    const name = await askName({ title: t("dp_page_name_title"), label: t("dp_page_name_prompt") });
    if (!name || !S.pad) return;
    const r = await bc.call("vpads.create_page", { pad: S.sel, name });
    if (!r.ok) return fail(r);
    if (r.page != null) await switchPage(r.page);
    else { await loadPad(); renderMain(); }
  }

  async function renamePage(pg) {
    const name = await askName({ title: t("dp_rename_page_title"), label: t("dp_page_name_prompt"), value: pg.name });
    if (!name || name === pg.name) return;
    const r = await bc.call("vpads.rename_page", { pad: S.sel, page: pg.id, name });
    if (!r.ok) return fail(r);
    await loadPad(); renderMain();
  }

  async function deletePage(pg) {
    if (pg.id === MAIN_PAGE) return;
    if (!(await confirmDialog({ title: t("dp_delete_page_title"), message: t("dp_delete_page_confirm", { name: pg.name }),
      okLabel: t("dp_delete_page_btn"), danger: true }))) return;
    const r = await bc.call("vpads.delete_page", { pad: S.sel, page: pg.id });
    if (!r.ok) return fail(r);
    dropKey();
    await loadPad(); renderMain();
  }

  async function setGrid(rows, cols) {
    const r = await bc.call("vpads.set_grid", { pad: S.sel, rows, cols });
    if (!r.ok) fail(r);
    await loadPads(); await loadPad(); renderAll();
  }

  async function toggleEdit() {
    if (S.edit && S.dirty && !(await askDiscard())) return;
    S.edit = !S.edit; dropKey();
    renderMain();
  }

  function renderPad() {
    const p = S.pad;
    if (!p) {
      grid = null; gridShape = "";
      clear(stage);
      if (!S.pads.length) stage.append(h("div", { class: "vpempty" }, h("p", null, t("vp_empty")),
        h("button", { class: "btn primary", type: "button", onclick: newPad }, icon("plus"), t("vp_new_pad"))));
      return;
    }
    const shape = `${p.id}:${p.rows}x${p.cols}`;
    if (!grid || shape !== gridShape) {
      clear(stage);
      grid = padGrid({ rows: p.rows, cols: p.cols, keys: p.keys, onPress: onTile });
      grid.el.setAttribute("aria-label", t("vp_grid_label", { name: p.name }));
      gridShape = shape;
      stage.append(grid.el);
    } else grid.update(p.keys);
    for (const b of grid.el.querySelectorAll(".tile")) {
      const idx = Number(b.dataset.idx), k = keyOf(idx);
      const sel = S.edit && S.key != null && idx === S.key && S.keyPage === p.current_page;
      b.classList.toggle("sel", sel);
      // A key with an action but neither picture nor text shows its type, short (as on the DisplayPad's screen).
      const on = !!(k.action && k.action.type && k.action.type !== "none");
      b.classList.toggle("assigned", on);
      const old = b.querySelector(".vplbl");
      if (old) old.remove();
      if (on && !k.image && !k.text) b.querySelector(".face").append(h("span", { class: "vplbl", "aria-hidden": "true" }, typeLabel(S.types, k.action.type)));
      if (S.edit) b.setAttribute("aria-pressed", sel ? "true" : "false"); else b.removeAttribute("aria-pressed");
    }
  }

  async function onTile(idx) {
    if (!S.pad) return;
    if (!S.edit) {
      const r = await bc.call("vpads.press", { pad: S.sel, key: idx });
      if (!r.ok) fail(r);
      return;
    }
    if (S.key === idx && S.keyPage === S.pad.current_page) return;
    if (S.dirty && !(await askDiscard())) return;
    S.key = idx; S.keyPage = S.pad.current_page; S.dirty = false; S.draft = makeDraft(keyOf(idx));
    renderPad(); renderInspector();
  }

  function makeDraft(k) {
    return { action: k.action || {}, image: undefined };   // image: undefined = unchanged, a path = new, null = cleared
  }

  /** The form's action as a virtual pad keeps it: page targets by id (renaming a page keeps them). */
  function padAction(a) {
    const out = { type: a.type, action: a.action };
    if (a.type === "page") {
      const pg = pageOf(a.target) || pageOf(a.action);
      out.target = pg ? pg.id : MAIN_PAGE;
      if (pg) out.action = pg.name;
    }
    const d = a.double;
    if (d && d.type && d.type !== "none") {
      const dbl = { type: d.type, action: d.action };
      if (d.type === "page") { const pg = pageOf(d.target) || pageOf(d.action); if (pg) dbl.target = pg.id; }
      out.double = dbl;
    }
    if (Array.isArray(a.actions) && a.actions.length) out.actions = a.actions;
    return out;
  }

  function textCfg(k) {
    return k.text ? { ...TEXT_DEFAULT, ...(k.text_style || {}), text: k.text } : null;
  }

  function renderInspector() {
    clear(inspHost);
    const p = S.pad;
    fr.main.classList.toggle("vpediting", !!(p && S.edit));
    if (!p || !S.edit) { hint.textContent = p ? t("vp_hint_press") : ""; return; }
    hint.textContent = t("vp_hint_edit");
    if (S.key == null) {
      inspHost.append(h("div", { class: "inspector" }, h("div", { class: "head" }, h("span", { class: "acc" }), h("span", { class: "t h" }, t("dp_inspector_title"))),
        h("div", { class: "empty", style: "padding-left:0" }, t("dp_key_select"))));
      return;
    }
    const i = S.key, page = S.keyPage, d = S.draft, k = keyOf(i);
    const n = keyNumber(i, p.cols);
    const touch = () => { S.dirty = true; };
    const form = actionForm({ types: S.types, value: d.action, pages: p.pages, newPage: false,
      onChange: () => { d.action = form.value(); touch(); } });

    // picture
    const thumb = h("div", { class: "thumb" });
    let text = textCfg(k);
    const showThumb = async () => {
      const path = d.image === undefined ? k.image : d.image;
      const url = path ? await bc.image(path, 112) : null;
      if (!thumb.isConnected) return;
      clear(thumb);
      if (url) thumb.append(h("img", { src: url, alt: "" }));
      const lay = textLayer(text); if (lay) thumb.append(lay);
    };
    const picked = (path) => { if (path) { d.image = path; touch(); showThumb(); } };
    const pickBtn = h("button", { class: "btn", type: "button", onclick: async () => picked(await bc.pickFile({ title: t("dp_insp_pick_image"), kind: "image" })) },
      t("dp_insp_pick_image"));
    const libBtn = h("button", { class: "btn", type: "button", onclick: async () => picked(await pickFromLibrary()) }, t("dp_insp_from_library"));
    const clrBtn = h("button", { class: "btn", type: "button", onclick: () => { d.image = null; touch(); showThumb(); } }, t("dp_insp_clear"));

    // The "T" under the picture: text over it, saved by its own OK (as on the DisplayPad).
    const tHost = textButton({ text: () => text, fonts: () => S.fonts, save: async (cfg) => {
      const style = cfg ? { ...cfg } : {};
      delete style.text;
      const r = await bc.call("vpads.set_key", { pad: S.sel, key: i, page, text: cfg ? cfg.text : "", text_style: style });
      if (!r.ok) { fail(r); return false; }
      text = cfg;
      toast(t("vp_text_saved", { k: n }), "ok");
      showThumb();
      return true;
    } });

    const apply = h("button", { class: "cta inline", type: "button", onclick: async () => {
      apply.disabled = true;
      const params = { pad: S.sel, key: i, page, action: padAction(form.value()) };
      if (d.image !== undefined) params.image = d.image || "";
      const r = await bc.call("vpads.set_key", params);
      apply.disabled = false;
      if (!r.ok) return fail(r);
      toast(t("dp_act_saved", { k: n }), "ok");
      S.dirty = false;
      await loadPad();
      if (S.key === i) S.draft = makeDraft(keyOf(i));
      renderMain();
    } }, t("dp_apply"));

    inspHost.append(h("div", { class: "inspector" },
      h("div", { class: "head" }, h("span", { class: "acc" }), h("h3", { class: "t h", style: "margin:0;font-weight:400" }, t("dp_key_title", { n })),
        h("div", { class: "headacts" }, h("button", { class: "btn", type: "button", onclick: () => clearKey(i, page) }, t("dp_clear_key")), apply)),
      h("div", { class: "grid2" },
        form.el,
        h("div", { class: "wide" }, h("span", { class: "lab" }, t("dp_insp_image")),
          h("div", { class: "picrow" }, h("div", { class: "thumbcol" }, thumb, tHost), pickBtn, libBtn, clrBtn)))));
    showThumb();
  }

  async function clearKey(i, page) {
    const r = await bc.call("vpads.clear_key", { pad: S.sel, key: i, page });
    if (!r.ok) return fail(r);
    await loadPad();
    if (S.key === i) { S.draft = makeDraft(keyOf(i)); S.dirty = false; }
    renderMain();
  }

  function renderMain() { renderBar(); renderPad(); renderInspector(); }
  function renderAll() { renderList(); renderMain(); }

  // ── events ───────────────────────────────────────────────────────────────
  const timers = new Map();
  const soon = (key, fn, ms = 80) => { sc.cancel(timers.get(key)); timers.set(key, sc.later(fn, ms)); };

  sc.on("vpads.changed", (_t, p) => {
    const what = p && p.what;
    soon("changed", async () => {
      if (!what || what === "list" || what === "grid") await loadPads();
      if (!what || what === "list") await loadWins();
      if (!p || !p.pad || p.pad === S.sel || !S.pad) {
        await loadPad();
        if (S.key != null && !S.dirty) S.draft = makeDraft(keyOf(S.key));
        if (S.dirty) { renderBar(); renderPad(); } else renderMain();
      }
      renderList();
    });
  });
  sc.on("vpads.page", (_t, p) => {
    if (!p || p.pad !== S.sel) return;
    soon("page", async () => {
      if (!S.dirty) dropKey();
      await loadPad(); renderBar(); renderPad();
      if (!S.dirty) renderInspector();
    });
  });
  sc.on("vpads.pressed", (_t, p) => { if (p && p.pad === S.sel && grid) grid.pressed(p.key); });
  sc.on("shell.pad_windows_changed", (_t, p) => { if (p && setWins(p.open)) { renderList(); renderBar(); } });
  // The fallback: asked again when the main window gets the focus back.
  const onFocus = () => soon("wins", async () => { if (await loadWins()) { renderList(); renderBar(); } }, 150);
  window.addEventListener("focus", onFocus);
  document.addEventListener("visibilitychange", onFocus);
  sc.add(() => { window.removeEventListener("focus", onFocus); document.removeEventListener("visibilitychange", onFocus); });

  // ── start ────────────────────────────────────────────────────────────────
  renderAll();
  const [, tr] = await Promise.all([loadPads(), bc.call("displaypad.action_types"), loadWins()]);
  if (!sc.alive) return;
  S.types = padTypes(parseTypes(tr));
  await loadPad();
  if (!sc.alive) return;
  renderAll();
  bc.call("displaypad.fonts").then((r) => {
    if (r && r.ok && Array.isArray(r.fonts) && r.fonts.length) S.fonts = r.fonts;
  });
}

export function unmount() { if (scope) scope.dispose(); scope = null; }
