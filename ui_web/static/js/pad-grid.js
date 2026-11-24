// A pad as a grid of square key tiles: the phone page's DisplayPad and virtual pads.
//
//   padGrid({rows, cols, keys, onPress, onEdit, keyImage}) -> {el, update(keys), pressed(idx)}
//
// keys: {idx: {image, text, text_style, rev}}, idx = row * 8 + col whatever the grid's size (the
// virtual pads' numbering; the caller maps the real DisplayPad's 0..11 onto it). `text` is a
// string styled by `text_style` (virtual pads) or the DisplayPad's text object {text, pos, font,
// size_pt, ...}. A changed `rev` fetches the same picture again (a live frame of a widget).
// onPress(idx) runs on a tap; onEdit(idx), when given, runs on a tap instead (editing, step 2).
// keyImage(path) -> Promise of a URL; bc.image(path, 256) without it.
//
// The tiles fill the grid's box (the box sets container-type: size): as large as fit in its
// width and its height, so they stay square in portrait and in landscape.
import bc from "./bridge.js";
import { t } from "./i18n.js";
import { h, clear } from "./ui.js";
import { TEXT_DEFAULT, textLayer } from "./key-text.js";

export const GRID_COLS = 8;

function textOf(key) {
  if (!key) return null;
  if (key.text && typeof key.text === "object") return key.text.text ? key.text : null;
  if (typeof key.text === "string" && key.text.trim()) {
    const style = key.text_style && typeof key.text_style === "object" ? key.text_style : {};
    return { ...TEXT_DEFAULT, ...style, text: key.text };
  }
  return null;
}

export function padGrid({ rows, cols, keys, onPress, onEdit, keyImage }) {
  rows = Math.max(1, rows | 0);
  cols = Math.max(1, cols | 0);
  const fetchImage = keyImage || ((path) => bc.image(path, 256));
  const el = h("div", { class: "padgrid", role: "group", style: `--rows:${rows};--cols:${cols}` });
  const tiles = new Map();          // idx -> {btn, face, img, src, rev, label, want}

  for (let r = 0; r < rows; r++) {
    for (let c = 0; c < cols; c++) {
      const idx = r * GRID_COLS + c;
      const img = h("img", { alt: "", draggable: "false", hidden: true });
      const face = h("span", { class: "face" }, img);
      const btn = h("button", { class: "tile empty", type: "button", "data-idx": String(idx) }, face);
      btn.addEventListener("click", () => {
        if (onEdit) onEdit(idx);
        else if (onPress) onPress(idx);
      });
      // The tile goes down under the finger at once; the press itself is the click.
      btn.addEventListener("pointerdown", () => btn.classList.add("down"));
      for (const ev of ["pointerup", "pointercancel", "pointerleave"]) btn.addEventListener(ev, () => btn.classList.remove("down"));
      btn.addEventListener("animationend", () => btn.classList.remove("hit"));
      btn.addEventListener("contextmenu", (e) => e.preventDefault());
      tiles.set(idx, { btn, face, img, src: null, rev: null, text: null, want: 0 });
      el.append(btn);
    }
  }

  function setKey(idx, key) {
    const tile = tiles.get(idx);
    if (!tile) return;
    const path = key && typeof key.image === "string" && key.image ? key.image : null;
    const rev = key && key.rev != null ? key.rev : null;
    const cfg = textOf(key);
    const label = (cfg && cfg.text) || t("remote_key", { n: tileNumber(idx) });
    tile.btn.setAttribute("aria-label", label);
    tile.btn.classList.toggle("empty", !path && !cfg);
    const textKey = cfg ? JSON.stringify(cfg) : null;
    if (textKey !== tile.text) {
      tile.text = textKey;
      const old = tile.face.querySelector(".keytext");
      if (old) old.remove();
      const layer = textLayer(cfg);
      if (layer) tile.face.append(layer);
    }
    if (path === tile.src && rev === tile.rev) return;
    tile.src = path; tile.rev = rev;
    const want = ++tile.want;            // a later picture wins over a slower earlier one
    if (!path) { tile.img.hidden = true; tile.img.removeAttribute("src"); return; }
    fetchImage(path).then((url) => {
      if (want !== tile.want) return;
      if (url) { tile.img.src = url; tile.img.hidden = false; }
      else { tile.img.hidden = true; tile.img.removeAttribute("src"); }
    }, () => {});
  }

  function tileNumber(idx) {
    return Math.floor(idx / GRID_COLS) * cols + (idx % GRID_COLS) + 1;
  }

  function update(next) {
    const all = next && typeof next === "object" ? next : {};
    for (const idx of tiles.keys()) setKey(idx, all[idx] || all[String(idx)] || null);
  }

  function pressed(idx) {
    const tile = tiles.get(Number(idx));
    if (!tile) return;
    const b = tile.btn;
    b.classList.remove("hit");
    void b.offsetWidth;               // restart the flash
    b.classList.add("hit");
  }

  update(keys);
  return { el, update, pressed, destroy: () => clear(el) };
}
