// The image editor, laid out like Base Camp's "Bild auswählen" modal: a large preview with a crop box
// of the target's fixed aspect, the tools under it (rotate, flip, reset), and on the right the file
// picker and "Your pictures" (a category select and a thumbnail grid). The core does the work:
// images.preview shows the picture (animated for a GIF), images.prepare crops, turns and flips it,
// every frame of a GIF too (core/images.py).
//
//   const path = await openImageEditor({ target: "displaypad_key", current, title });
//
// target: "displaypad_key" (1:1), "everest_dkey" (1:1), "everest_main" (240:204) or
// "displaypad_fullscreen" (800:240, the whole DisplayPad panel).
// Resolves with the path of the prepared picture, or null when cancelled.
import bc from "./bridge.js";
import { t } from "./i18n.js";
import { h, clear, modal, toast, confirmDialog } from "./ui.js";

const ASPECTS = { displaypad_key: [1, 1], everest_dkey: [1, 1], everest_main: [240, 204], displaypad_fullscreen: [10, 3] };
const ALL = "\u0000all";
const OWN = "\u0000own";
const MIN_BOX = 12;          // smallest crop box side, in picture pixels

/** The code in front of a core error ("too_large: the picture is ..."), else the text. */
function errorText(err) {
  const m = /^([a-z_]+):/.exec(err || "");
  const key = m && "ie_err_" + m[1];
  const text = key ? t(key) : "";
  return text && text !== key ? text : t("ui_error", { err: err || "images" });
}

export function openImageEditor({ target = "displaypad_key", current = null, title } = {}) {
  const [aw, ah] = ASPECTS[target] || [1, 1];
  const aspect = aw / ah;

  // The picture as loaded, and what is done to it. The crop box is kept in the pixels of the
  // picture after rotation and flip, which is what images.prepare takes.
  const S = { path: null, w: 0, h: 0, animated: false, frames: 1, rot: 0, flip: false, crop: null, busy: false };
  let scale = 1;            // stage pixels per picture pixel
  let origin = { x: 0, y: 0 };

  const oriented = () => (S.rot % 180 ? [S.h, S.w] : [S.w, S.h]);

  // ── the stage ────────────────────────────────────────────────────────────
  const img = h("img", { class: "ie-img", alt: "", draggable: "false" });
  const canvas = h("div", { class: "ie-canvas" }, img);
  const box = h("div", { class: "ie-box", tabindex: "0", role: "group", "aria-label": t("ie_crop_box"), hidden: true },
    ["nw", "ne", "sw", "se"].map((k) => h("span", { class: "ie-handle " + k, "data-h": k })));
  const hint = h("div", { class: "ie-hint" }, t("ie_nopic"));
  const stage = h("div", { class: "ie-stage" }, canvas, box, hint);

  function layout() {
    if (!S.path || !S.w) return;
    const size = stage.clientWidth;
    if (!size) return;
    const [ow, oh] = oriented();
    scale = Math.min(size / ow, size / oh);
    origin = { x: (size - ow * scale) / 2, y: (size - oh * scale) / 2 };
    canvas.style.cssText = `left:${origin.x}px;top:${origin.y}px;width:${ow * scale}px;height:${oh * scale}px`;
    img.style.cssText = `width:${S.w * scale}px;height:${S.h * scale}px;left:${(ow - S.w) * scale / 2}px;top:${(oh - S.h) * scale / 2}px;` +
      `transform:${S.flip ? "scaleX(-1) " : ""}rotate(${S.rot}deg)`;
    paintBox();
  }

  function defaultCrop() {
    const [ow, oh] = oriented();
    let w = ow, hh = ow / aspect;
    if (hh > oh) { hh = oh; w = oh * aspect; }
    return { x: (ow - w) / 2, y: (oh - hh) / 2, w, h: hh };
  }

  function paintBox() {
    const c = S.crop;
    if (!c) { box.hidden = true; return; }
    box.hidden = false;
    box.style.cssText = `left:${origin.x + c.x * scale}px;top:${origin.y + c.y * scale}px;width:${c.w * scale}px;height:${c.h * scale}px`;
  }

  // Drag the box, or a corner of it (the aspect stays).
  let drag = null;
  const toPicture = (e) => {
    const r = stage.getBoundingClientRect();
    return { x: (e.clientX - r.left - origin.x) / scale, y: (e.clientY - r.top - origin.y) / scale };
  };
  box.addEventListener("pointerdown", (e) => {
    if (!S.crop || e.button) return;
    const corner = e.target.dataset && e.target.dataset.h;
    const p = toPicture(e);
    drag = { corner, start: p, crop: { ...S.crop } };
    box.setPointerCapture(e.pointerId);
    box.focus();
    e.preventDefault();
  });
  box.addEventListener("pointermove", (e) => {
    if (!drag) return;
    const [ow, oh] = oriented();
    const p = toPicture(e);
    const c0 = drag.crop;
    if (!drag.corner) {
      S.crop = { ...c0,
        x: Math.max(0, Math.min(ow - c0.w, c0.x + p.x - drag.start.x)),
        y: Math.max(0, Math.min(oh - c0.h, c0.y + p.y - drag.start.y)) };
    } else {
      const sx = drag.corner.includes("e") ? 1 : -1, sy = drag.corner.includes("s") ? 1 : -1;
      const ax = sx > 0 ? c0.x : c0.x + c0.w, ay = sy > 0 ? c0.y : c0.y + c0.h;
      let w = Math.max(Math.abs(p.x - ax), Math.abs(p.y - ay) * aspect);
      w = Math.min(w, sx > 0 ? ow - ax : ax, (sy > 0 ? oh - ay : ay) * aspect);
      w = Math.max(w, Math.min(MIN_BOX, ow, oh * aspect));
      S.crop = { x: sx > 0 ? ax : ax - w, y: sy > 0 ? ay : ay - w / aspect, w, h: w / aspect };
    }
    paintBox();
  });
  const endDrag = () => { drag = null; };
  box.addEventListener("pointerup", endDrag);
  box.addEventListener("pointercancel", endDrag);
  box.addEventListener("keydown", (e) => {
    if (!S.crop) return;
    const [ow, oh] = oriented();
    const step = Math.max(1, Math.round(Math.max(ow, oh) / (e.shiftKey ? 200 : 40)));
    const c = { ...S.crop };
    if (e.key === "ArrowLeft") c.x -= step;
    else if (e.key === "ArrowRight") c.x += step;
    else if (e.key === "ArrowUp") c.y -= step;
    else if (e.key === "ArrowDown") c.y += step;
    else if (e.key === "+" || e.key === "-") {
      const w = Math.max(Math.min(MIN_BOX, ow, oh * aspect), Math.min(c.w + (e.key === "+" ? step : -step), ow, oh * aspect));
      c.x += (c.w - w) / 2; c.y += (c.h - w / aspect) / 2; c.w = w; c.h = w / aspect;
    } else return;
    c.x = Math.max(0, Math.min(ow - c.w, c.x)); c.y = Math.max(0, Math.min(oh - c.h, c.y));
    S.crop = c;
    paintBox();
    e.preventDefault(); e.stopPropagation();
  });

  // ── the tools ────────────────────────────────────────────────────────────
  function rotate(dir) {
    if (!S.path) return;
    const [ow, oh] = oriented();
    const c = S.crop;
    // The flip comes after the turn, so a visual turn to the right is a turn to the left once flipped.
    S.rot = (S.rot + ((dir > 0) !== S.flip ? 90 : 270)) % 360;
    if (c && aspect === 1) {      // a square box turns with the picture
      S.crop = dir > 0 ? { x: oh - (c.y + c.h), y: c.x, w: c.h, h: c.w } : { x: c.y, y: ow - (c.x + c.w), w: c.h, h: c.w };
    } else S.crop = defaultCrop();
    layout();
  }
  function flip() {
    if (!S.path) return;
    const [ow] = oriented();
    const c = S.crop;
    S.flip = !S.flip;
    // The flip is the last step, so the box mirrors within the turned picture.
    S.crop = { ...c, x: ow - (c.x + c.w) };
    layout();
  }
  function reset() {
    if (!S.path) return;
    S.rot = 0; S.flip = false; S.crop = defaultCrop();
    layout();
  }
  const tool = (name, icon, label, fn) => h("button", { class: "btn ie-tool", type: "button", "data-tool": name, title: label, "aria-label": label, disabled: true, onclick: fn }, icon);
  const tools = [
    tool("rotate-left", "↶", t("ie_rotate_left"), () => rotate(-1)),
    tool("flip", "⇆", t("ie_flip"), flip),
    tool("rotate-right", "↷", t("ie_rotate_right"), () => rotate(1)),
    tool("reset", "⟲", t("ie_reset"), reset),
  ];
  const info = h("div", { class: "help ie-info", "aria-live": "polite" });

  // ── loading a picture ────────────────────────────────────────────────────
  let loadToken = 0;
  async function load(path) {
    const token = ++loadToken;
    hint.hidden = false;
    hint.textContent = t("ie_loading");
    const r = await bc.call("images.preview", { path });
    if (token !== loadToken) return;
    if (!r || !r.ok) {
      hint.textContent = errorText(r && r.error);
      toast(errorText(r && r.error), "err");
      return;
    }
    Object.assign(S, { path, w: r.width, h: r.height, animated: !!r.animated, frames: r.frames || 1, rot: 0, flip: false });
    S.crop = defaultCrop();
    img.onload = () => { hint.hidden = true; layout(); };
    img.src = r.url;
    for (const b of tools) b.disabled = false;
    apply.disabled = false;
    fileName.textContent = path.split(/[\\/]/).pop();
    fileName.title = path;
    clear(info);
    info.append(`${r.width} × ${r.height} px`);
    if (S.animated) info.append(h("div", { class: "ie-gif" }, t("ie_gif", { n: S.frames })));
    layout();
  }

  // ── the right side: file, categories, thumbnails ───────────────────────────
  const fileName = h("span", { class: "ie-file" });
  const browse = h("button", { class: "btn", type: "button", id: "ie-browse", onclick: async () => {
    const p = await bc.pickFile({ title: t("ie_browse"), kind: "image" });
    if (p) load(p);
  } }, t("ie_browse"));

  const select = h("select", { class: "field", id: "ie-category", "aria-label": t("ie_category") });
  const grid = h("div", { class: "libgrid ie-grid" });
  let entries = [];
  let packs = [];
  const io = "IntersectionObserver" in window
    ? new IntersectionObserver((seen) => {
      for (const s of seen) if (s.isIntersecting) { io.unobserve(s.target); thumb(s.target); }
    }, { root: grid, rootMargin: "120px" })
    : null;
  function thumb(cell) {
    const im = cell.querySelector("img");
    if (!im || im.src) return;
    bc.image(cell.dataset.path, 96).then((url) => { if (url && im.isConnected) im.src = url; });
  }

  function fillSelect() {
    clear(select);
    select.append(h("option", { value: ALL }, t("dp_lib_all")));
    if (entries.some((e) => !e.pack)) select.append(h("option", { value: OWN }, t("dp_lib_own")));
    for (const p of packs) select.append(h("option", { value: p }, p));
  }
  function renderGrid() {
    if (io) io.disconnect();
    clear(grid);
    const f = select.value;
    const list = entries.filter((e) => f === ALL || (f === OWN ? !e.pack : e.pack === f));
    if (!list.length) { grid.append(h("div", { class: "empty" }, t("dp_lib_empty"))); return; }
    for (const e of list) {
      const label = e.pack ? `${e.name} (${e.pack})` : e.name;
      const cell = h("div", { class: "ie-cell", "data-path": e.path },
        h("button", { type: "button", class: "libcell", title: label, "aria-label": label, onclick: () => load(e.path) }, h("img", { alt: "" })));
      if (!e.pack) {
        cell.append(h("button", { type: "button", class: "ie-del", title: t("ie_delete"), "aria-label": t("ie_delete") + ": " + label,
          onclick: () => remove(e) }, "×"));
      }
      grid.append(cell);
      if (io) io.observe(cell); else thumb(cell);
    }
  }
  async function remove(e) {
    const ok = await confirmDialog({ title: t("ie_delete"), message: t("ie_delete_confirm", { name: e.name }), okLabel: t("dp_delete_page_btn"), danger: true });
    if (!ok) return;
    const r = await bc.call("images.delete", { path: e.path });
    if (!r.ok) { toast(errorText(r.error), "err"); return; }
    entries = entries.filter((x) => x.path !== e.path);
    if (S.path === e.path) { /* the picture stays loaded; only the library entry is gone */ }
    renderGrid();
  }
  async function loadLibrary() {
    const r = await bc.call("displaypad.library");
    if (!r || !r.ok) { clear(grid); grid.append(h("div", { class: "empty" }, t("dp_lib_empty"))); return; }
    entries = (Array.isArray(r.entries) ? r.entries : []).filter((e) => e && typeof e.path === "string");
    packs = Array.isArray(r.packs) ? r.packs : [...new Set(entries.map((e) => e.pack).filter(Boolean))];
    fillSelect();
    renderGrid();
  }
  select.addEventListener("change", renderGrid);

  // ── apply ────────────────────────────────────────────────────────────────
  const apply = h("button", { class: "btn primary", type: "button", id: "ie-apply", disabled: true, onclick: async () => {
    if (!S.path || S.busy) return;
    S.busy = true;
    apply.disabled = true;
    apply.textContent = t("ie_working");
    const c = S.crop;
    const r = await bc.call("images.prepare", {
      path: S.path, target, rotate: S.rot, flip: S.flip,
      crop: [Math.round(c.x), Math.round(c.y), Math.round(c.w), Math.round(c.h)],
    });
    S.busy = false;
    apply.textContent = t("ie_apply");
    if (!r.ok || !r.path) { apply.disabled = false; toast(errorText(r.error), "err"); return; }
    closeDialog(r.path);
  } }, t("ie_apply"));

  // ── the dialog ───────────────────────────────────────────────────────────
  let closeDialog = () => {};
  return new Promise((resolve) => {
    const left = h("div", { class: "ie-left" }, stage,
      h("div", { class: "ie-tools", role: "toolbar", "aria-label": t("ie_tools") }, tools), info);
    const right = h("div", { class: "ie-right" },
      h("span", { class: "lab" }, t("ie_file")),
      h("div", { class: "inline-row ie-filerow" }, browse, fileName),
      h("span", { class: "lab", style: "margin-top:18px" }, t("ie_yours")),
      select, grid);
    const { dlg, close } = modal({
      title: title || t("ie_title"),
      body: h("div", { class: "ie-body" }, left, right),
      buttons: (c) => [
        h("button", { class: "btn", type: "button", id: "ie-cancel", onclick: () => c(null) }, t("ui_cancel")),
        apply,
      ],
      onClose: (path) => {
        if (io) io.disconnect();
        if (ro) ro.disconnect();
        resolve(typeof path === "string" ? path : null);
      },
    });
    closeDialog = close;
    dlg.classList.add("wide", "ie");
    const ro = "ResizeObserver" in window ? new ResizeObserver(layout) : null;
    if (ro) ro.observe(stage);
    loadLibrary();
    if (current && typeof current === "string") load(current);
    browse.focus();
  });
}
