// DisplayPad, key binding: pages column, key grid with pictures, key inspector.
import bc from "../bridge.js";
import { t } from "../i18n.js";
import * as devices from "../devices.js";
import { h, icon, esc, clear, toast, makeScope, promptDialog, confirmDialog } from "../ui.js";
import { frame, statusBar } from "./dp-common.js";
import { PAD_DRAWING, drawingRects, photoRects } from "../hotspots.js";
import { pickFromLibrary } from "../library-picker.js";
import { openImageEditor } from "../image-editor.js";
import { TEXT_FONTS_FALLBACK, textLayer, textButton } from "../key-text.js";
import { FALLBACK_TYPES, parseTypes, typeLabel, actionForm } from "../key-action-form.js";

const NUM_KEYS = 12;
const MAIN_PAGE = 0;

let scope = null;

export async function mount(el) {
  scope = makeScope();
  const fr = frame(el, "keys", true);
  const S = {
    pages: [], current: 0, view: 0, keys: new Map(),   // page id -> [{idx, image, action}]
    types: FALLBACK_TYPES, sel: null, draft: null, dirty: false,
    imgs: new Map(),                                   // "page:idx" -> {path, url}
    present: null, padPhoto: null,
    fs: new Map(),                                     // page id -> {kind: "gif"|"picture", path} (the page's fullscreen)
    panel: new Map(),                                  // page id -> {w, h, keys: [[l, t, w, h]]} of the panel picture
    fsUrl: new Map(),                                  // fullscreen path -> data URL of its (first) frame
    fonts: TEXT_FONTS_FALLBACK,
  };
  const sb = statusBar(scope, (st) => { S.present = st.present; });
  const padHost = h("div", { style: "width:100%;display:flex;flex-direction:column;align-items:center" });
  const inspHost = h("div", { style: "width:100%" });
  fr.main.append(sb.el, padHost, inspHost);

  const pageName = (id) => (S.pages.find((p) => p.id === id) || {}).name || String(id);

  // ── data ────────────────────────────────────────────────────────────────
  async function loadKeys(page) {
    const r = await bc.call("displaypad.keys", { page });
    if (!scope.alive) return;
    const arr = r.ok && Array.isArray(r.keys) ? r.keys : [];
    const keys = [];
    for (let i = 0; i < NUM_KEYS; i++) keys.push(arr.find((k) => k.idx === i) || { idx: i, image: null, action: { type: "none", action: "" } });
    S.keys.set(page, keys);
    if (r.ok && r.fullscreen && typeof r.fullscreen === "object") { S.fs.set(page, r.fullscreen); if (r.panel) S.panel.set(page, r.panel); }
    else { S.fs.delete(page); S.panel.delete(page); }
  }

  async function loadPages() {
    const r = await bc.call("displaypad.list_pages");
    if (!scope.alive) return;
    if (!r.ok) { toast(t("ui_error", { err: r.error }), "err"); return; }
    S.pages = (r.pages || []).map((p) => ({ id: p.id, name: p.name }));
    S.current = r.current;
    if (!S.pages.some((p) => p.id === S.view)) S.view = S.pages.some((p) => p.id === S.current) ? S.current : (S.pages[0] || {}).id;
    await Promise.all(S.pages.map((p) => loadKeys(p.id)));
    for (const k of [...S.keys.keys()]) if (!S.pages.some((p) => p.id === k)) S.keys.delete(k);
  }

  const keyOf = (idx, page = S.view) => ((S.keys.get(page) || [])[idx]) || { idx, image: null, action: { type: "none", action: "" } };
  const assigned = (k) => !!(k.action && k.action.type && k.action.type !== "none");

  // pictures: fetched when the path changes (or forced by a key_frame event)
  async function ensureImg(page, idx, force, size = 128) {
    const k = keyOf(idx, page);
    const id = page + ":" + idx;
    const path = typeof k.image === "string" ? k.image : null;
    const have = S.imgs.get(id);
    if (!path) { S.imgs.delete(id); return null; }
    if (have && have.path === path && !force) return have.url;
    const url = await bc.image(path, size);
    S.imgs.set(id, { path, url });
    return url;
  }

  // ── pages column ─────────────────────────────────────────────────────────
  function renderPages() {
    const col = fr.col;
    const keepFocus = col.contains(document.activeElement) ? document.activeElement.getAttribute("data-f") : null;
    clear(col);
    col.append(
      h("div", { class: "head" }, h("span", { class: "acc" }), h("h2", { class: "t h", style: "margin:0;font-weight:400" }, t("dp_pages_title"))),
      h("button", { class: "newbtn", type: "button", onclick: newPage }, icon("plus"), t("dp_new_page").replace(/^＋\s*/, "")));
    const rows = h("div", { class: "rows" });
    for (const p of S.pages) {
      const keys = S.keys.get(p.id) || [];
      const n = keys.filter(assigned).length;
      const onPad = p.id === S.current;
      const tile = h("div", { class: "tile" }, icon("shape"));
      const first = keys.find((k) => typeof k.image === "string");
      if (first) ensureImg(p.id, first.idx, false, 64).then((url) => {
        if (url && tile.isConnected) { clear(tile); tile.append(h("img", { src: url, alt: "" })); }
      });
      rows.append(h("div", { class: "row" + (p.id === S.view ? " on" : "") },
        h("button", { class: "pick", type: "button", "data-f": "pick" + p.id, "aria-pressed": p.id === S.view ? "true" : "false",
          onclick: () => selectPage(p.id) },
          tile,
          h("div", { class: "txt" },
            h("div", { class: "nm h" }, p.name),
            h("div", { class: "sub" }, onPad ? h("span", { class: "badge", style: "margin:0 6px 0 0" }, t("dp_on_pad")) : null, t("dp_page_summary", { keys: NUM_KEYS, n })))),
        h("div", { class: "acts" },
          iconBtn("play", t("dp_switch_to_page", { name: p.name }), () => switchTo(p.id), onPad),
          iconBtn("pencil", t("dp_rename_page_btn") + ": " + p.name, () => renamePage(p), false),
          iconBtn("trash", t("dp_delete_page_btn") + ": " + p.name, () => deletePage(p), p.id === MAIN_PAGE))));
    }
    col.append(rows);
    if (keepFocus) { const f = col.querySelector(`[data-f="${keepFocus}"]`); if (f) f.focus(); }
  }

  function iconBtn(name, label, fn, disabled) {
    return h("button", { class: "iconbtn", type: "button", title: label, "aria-label": label, disabled, onclick: fn }, icon(name));
  }

  async function selectPage(id) {
    if (S.dirty && !(await confirmDialog({ title: t("dp_discard_title"), message: t("dp_discard_msg"), okLabel: t("dp_discard_btn") }))) return;
    S.view = id; S.sel = null; S.draft = null; S.dirty = false;
    await loadKeys(id);
    renderAll();
  }

  async function newPage() {
    const title = await promptDialog({ title: t("dp_page_name_title"), label: t("dp_page_name_prompt") });
    if (!title) return;
    const r = await bc.call("displaypad.create_page", { title });
    if (!r.ok) return toast(t("ui_error", { err: r.error }), "err");
    await loadPages();
    if (r.page != null) { S.view = r.page; S.sel = null; S.draft = null; S.dirty = false; await loadKeys(r.page); }
    renderAll();
  }

  async function renamePage(p) {
    const title = await promptDialog({ title: t("dp_rename_page_title"), label: t("dp_page_name_prompt"), value: p.name });
    if (!title || title === p.name) return;
    const r = await bc.call("displaypad.rename_page", { page: p.id, title });
    if (!r.ok) return toast(t("ui_error", { err: r.error }), "err");
    await loadPages(); renderAll();
  }

  async function deletePage(p) {
    if (p.id === MAIN_PAGE) return toast(t("dp_delete_page_main_error"), "err");
    const refs = await bc.call("displaypad.page_references", { page: p.id });
    const list = refs.ok && Array.isArray(refs.references) ? refs.references : [];
    const kinds = { action: "dp_ref_kind_action", also_on_press: "dp_ref_kind_also_on_press", double_click: "dp_ref_kind_double_click", timeout: "dp_ref_kind_timeout" };
    const lines = list.slice(0, 8).map((x) => `${pageName(x.from_page)}${x.key != null ? " · " + t("dp_key_n", { n: x.key + 1 }) : ""}: ${t(kinds[x.kind] || "dp_ref_kind_action")}`);
    if (list.length > 8) lines.push(t("dp_delete_page_more", { count: list.length - 8 }));
    const message = list.length
      ? t("dp_delete_page_referenced_warning", { name: p.name, count: list.length })
      : t("dp_delete_page_confirm", { name: p.name });
    if (!(await confirmDialog({ title: t("dp_delete_page_title"), message, details: lines, okLabel: t("dp_delete_page_btn"), danger: true }))) return;
    const r = await bc.call("displaypad.delete_page", { page: p.id });
    if (!r.ok) return toast(t("ui_error", { err: r.error }), "err");
    if (S.view === p.id) { S.view = MAIN_PAGE; S.sel = null; S.draft = null; S.dirty = false; }
    await loadPages(); renderAll();
  }

  async function switchTo(id) {
    const r = await bc.call("displaypad.switch_page", { page: id });
    if (!r.ok) toast(t("ui_error", { err: r.error }), "err");
  }

  // ── key grid: the picture (drawing or photo) with a layer of buttons over it ─────────
  // S.padPhoto = {url, rects} when an imported photo with a known hotspot table is in use.
  function renderPad() {
    const focusIdx = padHost.contains(document.activeElement) ? document.activeElement.getAttribute("data-idx") : null;
    const photoMode = !!S.padPhoto;
    const rects = photoMode ? S.padPhoto.rects : drawingRects("displaypad", "page");
    clear(padHost);

    let picture;
    if (photoMode) picture = h("img", { class: "padpic", src: S.padPhoto.url, alt: "", draggable: "false" });
    else {
      const tpl = document.createElement("template");
      tpl.innerHTML =
        `<svg class="padpic" viewBox="0 0 ${PAD_DRAWING.w} ${PAD_DRAWING.h}" aria-hidden="true"><defs>` +
        `<linearGradient id="pb" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#4a4f5e"/><stop offset=".55" stop-color="#23262f"/><stop offset="1" stop-color="#121318"/></linearGradient></defs>` +
        `<path d="M280 0 v18" stroke="#111" stroke-width="10"/>` +
        `<rect x="10" y="14" width="540" height="270" rx="26" fill="url(#pb)" stroke="#5d6375"/>` +
        `<rect x="36" y="50" width="488" height="190" rx="10" fill="#101217"/>` +
        `<path d="M210 262 L350 262 L336 270 L224 270Z" fill="#3a3f4d"/></svg>`;
      picture = tpl.content.firstChild;
    }
    const layer = h("div", { class: "hotspots", role: "group", "aria-label": t("dp_grid_label") });
    const fs = S.fs.get(S.view) || null;
    for (let i = 0; i < NUM_KEYS; i++) {
      const [x, y, w, hh] = rects[i];
      const k = keyOf(i), on = assigned(k), sel = S.sel === i;
      const typ = on ? typeLabel(S.types, k.action.type) : t("dp_key_empty");
      const label = t("dp_key_n", { n: i + 1 }) + ": " + typ + (k.action && k.action.action ? " " + k.action.action : "");
      const btn = h("button", { type: "button", class: "hs" + (on ? " assigned" : "") + (sel ? " sel" : "") + (photoMode ? " onphoto" : ""),
        "data-idx": i, "aria-label": label, "aria-pressed": sel ? "true" : "false",
        style: `left:${x * 100}%;top:${y * 100}%;width:${w * 100}%;height:${hh * 100}%`,
        onclick: () => selectKey(i), oncontextmenu: (e) => { e.preventDefault(); clearKey(i); } },
        h("span", { class: "face" }, h("img", { alt: "", hidden: true }),
          // A page key without a picture of its own gets the pad's folder sign (the core draws the
          // real one when that page comes up); other keys show their type, short.
          on && k.action.type === "page" ? h("span", { class: "lbl lblicon" }, icon("folder")) : on ? h("span", { class: "lbl" }, typ.slice(0, 9)) : null,
          textLayer(k.text)),
        h("span", { class: "num", "aria-hidden": "true" }, i + 1));
      if (fs) btn.classList.add("fsk");
      layer.append(btn);
    }
    const box = h("div", { class: "padbox" + (photoMode ? " photo" : "") }, picture, layer);
    padHost.append(box,
      h("div", { class: "padfoot" },
        h("div", { class: "legend" },
          h("span", null, h("i", { style: "background:#3a3f4d" }), t("dp_legend_default")),
          h("span", null, h("i", { style: "border:2px solid #fff" }), t("dp_legend_selected")),
          h("span", null, h("i", { style: "background:var(--logo-blue)" }), t("dp_legend_assigned"))),
        fullscreenBar(fs)),
      h("div", { class: "gridhint" }, t("dp_grid_hint")));
    if (fs && fs.path) {
      // A fullscreen picture or GIF: each key shows its window of the panel (the first frame of a GIF).
      const pg = S.panel.get(S.view) || { w: 800, h: 240, keys: null };
      fullscreenUrl(fs.path).then((url) => {
        if (!url) return;
        for (let i = 0; i < NUM_KEYS; i++) {
          const btn = layer.querySelector(`.hs[data-idx="${i}"]`);
          const im = btn && btn.querySelector("img");
          if (!im) continue;
          im.src = url; im.hidden = false; btn.classList.add("haspic");
          // The window is a 102 px key in panel pixels; the geometry comes from the core (approximate).
          const [kl, kt, kw, kh] = (pg.keys && pg.keys[i]) || [i % 6 * 139.6, Math.floor(i / 6) * 138, 102, 102];
          im.style.cssText = `width:${pg.w / kw * 100}%;height:${pg.h / kh * 100}%;left:${-kl / kw * 100}%;top:${-kt / kh * 100}%;object-fit:fill`;
        }
      });
    } else {
      for (let i = 0; i < NUM_KEYS; i++) {
        ensureImg(S.view, i, false).then((url) => {
          const btn = layer.querySelector(`.hs[data-idx="${i}"]`);
          if (!btn) return;
          const im = btn.querySelector("img");
          if (url) { im.src = url; im.hidden = false; btn.classList.add("haspic"); }
        });
      }
    }
    if (focusIdx != null) { const b = padHost.querySelector(`.hs[data-idx="${focusIdx}"]`); if (b) b.focus(); }
  }

  // ── fullscreen: one picture or GIF over the twelve keys of the page on view ──────
  async function fullscreenUrl(path) {
    if (!S.fsUrl.has(path)) S.fsUrl.set(path, await bc.image(path, 800));
    return S.fsUrl.get(path);
  }

  /** The row under the grid: a badge when the page is in fullscreen, the pick button, the remove button. */
  function fullscreenBar(fs) {
    const pick = h("button", { class: "btn fsbtn", type: "button", "data-f": "fs-pick", title: t("dp_pick_fullscreen"), onclick: pickFullscreen }, t("dp_fullscreen"));
    const rm = h("button", { class: "btn fsbtn", type: "button", "data-f": "fs-clear", disabled: !fs, onclick: removeFullscreen }, t("dp_fullscreen_remove"));
    return h("div", { class: "fsbar" },
      fs ? h("span", { class: "badge fsbadge" }, fs.kind === "gif" ? t("dp_fullscreen_badge_gif") : t("dp_fullscreen_badge")) : null,
      pick, rm);
  }

  async function pickFullscreen() {
    if (S.dirty && !(await confirmDialog({ title: t("dp_discard_title"), message: t("dp_discard_msg"), okLabel: t("dp_discard_btn") }))) return;
    const cur = S.fs.get(S.view);
    // The editor's crop box has the whole panel's shape (800 x 240); a GIF keeps every frame.
    const p = await openImageEditor({ target: "displaypad_fullscreen", current: cur && cur.path || null, title: t("dp_pick_fullscreen") });
    if (!p) return;
    const page = S.view;
    const r = await bc.call("displaypad.set_fullscreen", { path: p, page });
    if (!r.ok) return toast(t("ui_error", { err: r.error }), "err");
    toast(t(r.kind === "gif" ? "dp_fullscreen_gif" : "dp_fullscreen_static", { name: p.split(/[\\/]/).pop() }), "ok");
    await reloadFullscreen(page);
  }

  async function removeFullscreen() {
    const page = S.view;
    const r = await bc.call("displaypad.clear_fullscreen", { page });
    if (!r.ok) return toast(t("ui_error", { err: r.error }), "err");
    toast(t("dp_fullscreen_removed"), "ok");
    await reloadFullscreen(page);
  }

  async function reloadFullscreen(page) {
    for (const k of [...S.imgs.keys()]) if (k.startsWith(page + ":")) S.imgs.delete(k);
    await loadKeys(page);
    if (!scope.alive) return;
    if (page === S.view) {
      if (S.sel != null && !S.dirty) S.draft = makeDraft(keyOf(S.sel));
      renderAll();
    } else renderPages();
  }

  async function selectKey(i) {
    if (S.sel !== i && S.dirty && !(await confirmDialog({ title: t("dp_discard_title"), message: t("dp_discard_msg"), okLabel: t("dp_discard_btn") }))) return;
    S.sel = i; S.dirty = false; S.draft = makeDraft(keyOf(i));
    renderPad(); renderInspector();
  }

  async function clearKey(i) {
    if (!assigned(keyOf(i)) && typeof keyOf(i).image !== "string") return;
    const r = S.view === S.current
      ? await bc.call("displaypad.clear_key", { key: i })
      : await bc.call("displaypad.set_action", { key: i, page: S.view, type: "none", action: "", double: { type: "none", action: "" } });
    if (!r.ok) return toast(t("ui_error", { err: r.error }), "err");
    if (S.view !== S.current) await bc.call("displaypad.set_image", { idx: i, path: null, page: S.view });
    await loadKeys(S.view);
    if (S.sel === i) { S.draft = makeDraft(keyOf(i)); S.dirty = false; }
    renderAll();
  }

  // ── inspector ────────────────────────────────────────────────────────────
  function makeDraft(k) {
    return {
      action: k.action || {},      // the action, its double press and chain (key-action-form.js)
      hint: typeof k.hint === "string" ? k.hint : "",
      text: k.text && typeof k.text === "object" ? k.text : null,   // the text over the picture (saved by its own OK)
      image: undefined,            // undefined = unchanged, string = new file, null = cleared
    };
  }

  function renderInspector() {
    clear(inspHost);
    if (S.sel == null) {
      inspHost.append(h("div", { class: "inspector" }, h("div", { class: "head" }, h("span", { class: "acc" }), h("span", { class: "t h" }, t("dp_inspector_title"))),
        h("div", { class: "empty", style: "padding-left:0" }, t("dp_key_select"))));
      return;
    }
    const i = S.sel, d = S.draft, k = keyOf(i);
    const touch = () => { S.dirty = true; };
    // type, value, double press; the "also on press" chain stays as the core has it (no `extra`)
    const form = actionForm({ types: S.types, value: d.action, pages: S.pages, onChange: () => { d.action = form.value(); touch(); } });

    // picture
    const thumb = h("div", { class: "thumb" });
    const showThumb = async () => {
      clear(thumb);
      let url = null;
      if (d.image === null) url = null;
      else if (typeof d.image === "string") url = await bc.image(d.image, 112);
      else url = await ensureImg(S.view, i, false, 112);
      if (url && thumb.isConnected) thumb.append(h("img", { src: url, alt: "" }));
      if (thumb.isConnected) { const lay = textLayer(d.text); if (lay) thumb.append(lay); }
    };
    const pickBtn = h("button", { class: "btn", type: "button", onclick: async () => {
      // The image editor (crop, rotate, flip; a GIF keeps every frame) hands back the prepared picture.
      const p = await openImageEditor({ target: "displaypad_key", current: typeof d.image === "string" ? d.image : null });
      if (p) { d.image = p; touch(); showThumb(); }
    } }, t("dp_insp_pick_image"));
    // A picture from the DisplayPad library (the Mountain icon pack among them), set like a picked file.
    const libBtn = h("button", { class: "btn", type: "button", onclick: async () => {
      const picked = await pickFromLibrary();
      const p = picked && await openImageEditor({ target: "displaypad_key", current: picked });
      if (p) { d.image = p; touch(); showThumb(); }
    } }, t("dp_insp_from_library"));
    const clrBtn = h("button", { class: "btn", type: "button", onclick: () => { d.image = null; touch(); showThumb(); } }, t("dp_insp_clear"));

    // The "T" under the picture: text drawn over it, with its own small form.
    const tHost = textButton({ text: () => d.text, fonts: () => S.fonts, save: async (text) => {
      const r = await bc.call("displaypad.set_key_text", { key: i, page: S.view, text });
      if (!r.ok) { toast(t("ui_error", { err: r.error }), "err"); return false; }
      d.text = r.text || null;
      toast(t("dp_text_saved", { k: i + 1 }), "ok");
      showThumb();
      return true;
    } });

    // The few words the page's hint box shows for this key (#93).
    const hintIn = h("input", { class: "field plain", id: "ins-hint", value: d.hint, maxlength: 60,
      placeholder: t("dp_insp_hint_ph") });
    hintIn.addEventListener("input", () => { d.hint = hintIn.value; touch(); });

    const apply = h("button", { class: "cta inline", type: "button", onclick: async () => {
      apply.disabled = true;
      const av = form.value();
      const params = { key: i, page: S.view, type: av.type, action: av.action, hint: d.hint };
      if (av.target !== undefined) params.target = av.target;
      params.double = av.double;
      let r = await bc.call("displaypad.set_action", params);
      if (r.ok && d.image !== undefined) r = await bc.call("displaypad.set_image", { idx: i, path: d.image, page: S.view });
      apply.disabled = false;
      if (!r.ok) return toast(t("ui_error", { err: r.error }), "err");
      toast(t("dp_act_saved", { k: i + 1 }), "ok");
      S.dirty = false;
      await loadPages();                    // a page action may have made a page
      await loadKeys(S.view);
      S.draft = makeDraft(keyOf(i));
      renderAll();
    } }, t("dp_apply"));

    inspHost.append(h("div", { class: "inspector" },
      h("div", { class: "head" }, h("span", { class: "acc" }), h("h3", { class: "t h", style: "margin:0;font-weight:400" }, t("dp_key_title", { n: i + 1 })),
        // Apply beside "clear key" in the head: a row of its own under the fields needed scrolling.
        h("div", { class: "headacts" }, h("button", { class: "btn", type: "button", onclick: () => clearKey(i) }, t("dp_clear_key")), apply)),
      h("div", { class: "grid2" },
        form.el,
        h("div", { class: "wide" }, h("label", { class: "lab", for: "ins-hint" }, t("dp_insp_hint")), hintIn),
        h("div", { class: "wide" }, h("span", { class: "lab" }, t("dp_insp_image")), h("div", { class: "picrow" }, h("div", { class: "thumbcol" }, thumb, tHost), pickBtn, libBtn, clrBtn)))));
    showThumb();
  }

  // ── photo (optional): used only when its source file has a hotspot table here ──────
  async function updatePhoto() {
    const rects = photoRects("displaypad", "page", devices.photoSource("displaypad", "page"));
    const url = rects ? await devices.photoUrl("displaypad", "page", 900) : null;
    if (!scope.alive) return;
    const next = url && rects ? { url, rects } : null;
    if ((next && next.url) !== (S.padPhoto && S.padPhoto.url)) { S.padPhoto = next; renderPad(); }
  }
  scope.add(devices.onPhotos(updatePhoto));

  function renderAll() { renderPages(); renderPad(); renderInspector(); }

  // ── events ───────────────────────────────────────────────────────────────
  const timers = new Map();
  const soon = (key, fn, ms = 80) => { scope.cancel(timers.get(key)); timers.set(key, scope.later(fn, ms)); };

  scope.on("displaypad.pages_changed", () => soon("pages", async () => { await loadPages(); renderAll(); }));
  scope.on("displaypad.key_changed", (_t, p) => {
    const page = p && p.page != null ? p.page : S.view;
    soon("key" + page, async () => {
      await loadKeys(page);
      if (page === S.view) {
        renderPad();
        if (S.sel != null && !S.dirty) { S.draft = makeDraft(keyOf(S.sel)); renderInspector(); }
      }
      renderPages();
    });
  });
  scope.on("displaypad.fullscreen_changed", (_t, p) => {
    const page = p && p.page != null ? p.page : S.view;
    soon("fs" + page, () => reloadFullscreen(page));
  });
  scope.on("displaypad.page_switched", (_t, p) => {
    const prev = S.current;
    S.current = p && p.page != null ? p.page : S.current;
    if (S.view === prev && !S.dirty && S.pages.some((x) => x.id === S.current)) {   // follow the pad
      S.view = S.current; S.sel = null; S.draft = null;
      loadKeys(S.view).then(renderAll);
    } else renderPages();
  });
  scope.on("displaypad.key_frame", (_t, p) => {
    if (!p || S.view !== S.current) return;
    soon("frame" + p.idx, async () => {
      await loadKeys(S.view);
      const url = await ensureImg(S.view, p.idx, true);
      const im = padHost.querySelector(`.hs[data-idx="${p.idx}"] img`);
      if (im && url) { im.src = url; im.hidden = false; }
    }, 120);
  });

  // ── start ────────────────────────────────────────────────────────────────
  renderPad(); renderInspector();
  const [, tr] = await Promise.all([loadPages(), bc.call("displaypad.action_types")]);
  if (!scope.alive) return;
  S.types = parseTypes(tr);
  renderAll();
  bc.call("displaypad.fonts").then((fr2) => {
    if (fr2 && fr2.ok && Array.isArray(fr2.fonts) && fr2.fonts.length) S.fonts = fr2.fonts;
  });
  await updatePhoto();
}

export function unmount() { if (scope) scope.dispose(); scope = null; }
