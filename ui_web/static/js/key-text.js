// Text over a key's picture (the "T" under it), shared by the DisplayPad keys and the Everest Max's
// D1-D4: the layer that draws it over a thumbnail, and the small form that edits it. The core draws
// the same text onto the picture the device gets (core/devices/displaypad/images.py).
import { t } from "./i18n.js";
import { h } from "./ui.js";

// Used when displaypad.fonts is not there.
export const TEXT_FONTS_FALLBACK = ["DejaVu Sans", "DejaVu Serif", "DejaVu Sans Mono", "Liberation Sans", "Liberation Serif", "Liberation Mono"];
export const TEXT_DEFAULT = { text: "", pos: "bottom", font: "DejaVu Sans", size_pt: 12, bold: false, italic: false, underline: false, color: "white" };

/** The text over a key, as a layer for a box that is 102 units wide in the core (the box sets container-type). */
export function textLayer(cfg) {
  if (!cfg || !cfg.text) return null;
  const px = Math.max(6, Math.round(cfg.size_pt * 4 / 3));
  const fg = cfg.color === "black" ? "#000" : "#fff", bg = cfg.color === "black" ? "#fff" : "#000";
  const cls = "keytext " + (cfg.pos === "top" ? "top" : cfg.pos === "center" ? "center" : "bottom");
  return h("span", { class: cls, "aria-hidden": "true",
    style: `font-family:"${String(cfg.font).replace(/"/g, "")}",sans-serif;font-size:${px * 0.6}px;font-size:${(px / 1.02).toFixed(2)}cqw;` +
      `color:${fg};text-shadow:1px 1px 0 ${bg};font-weight:${cfg.bold ? 700 : 400};font-style:${cfg.italic ? "italic" : "normal"};` +
      `text-decoration:${cfg.underline ? "underline" : "none"}` }, cfg.text);
}

export function closeTextPop(host, btn) {
  const p = host.querySelector(".textpop"); if (p) p.remove();
  btn.setAttribute("aria-expanded", "false");
}

/** The "T" button and its form, toggled in place. opts: {text: the current text (or null), fonts,
 *  save(text) -> Promise<bool> (true closes the form), up: open above the button}. Returns the host. */
export function textButton({ text, fonts, save, up }) {
  const btn = h("button", { class: "btn tbtn", type: "button", "aria-haspopup": "dialog", "aria-expanded": "false",
    title: t("dp_text_title"), "aria-label": t("dp_text_title") }, t("dp_text_btn"));
  const host = h("div", { class: "textpop-host" }, btn);
  btn.addEventListener("click", () => {
    if (host.querySelector(".textpop")) return closeTextPop(host, btn);
    host.append(textPopover(host, btn, { text: typeof text === "function" ? text() : text, fonts: typeof fonts === "function" ? fonts() : fonts, save, up }));
    btn.setAttribute("aria-expanded", "true");
    const f = host.querySelector("input[type=text]"); if (f) f.focus();
  });
  return host;
}

function textPopover(host, btn, { text, fonts, save, up }) {
  const cur = { ...TEXT_DEFAULT, ...(text || {}) };
  const lab = (id, key) => h("label", { class: "lab", for: id }, t(key));
  const pos = h("div", { class: "poschecks", role: "group", "aria-label": t("dp_text_pos") });
  const posBoxes = {};
  for (const p of ["top", "center", "bottom"]) {
    const cb = h("input", { type: "checkbox", id: "tx-pos-" + p, checked: cur.pos === p });
    cb.addEventListener("change", () => {          // one place at a time; unchecking keeps the choice
      cur.pos = p;
      for (const [q, c] of Object.entries(posBoxes)) c.checked = q === p;
    });
    posBoxes[p] = cb;
    pos.append(h("label", { class: "chk", for: "tx-pos-" + p }, cb, t("dp_text_pos_" + p)));
  }
  const txt = h("input", { class: "field", id: "tx-text", type: "text", maxlength: 50, value: cur.text,
    placeholder: t("dp_text_ph"), autocomplete: "off", spellcheck: "false" });
  const fontSel = h("select", { class: "field", id: "tx-font" });
  const list = fonts && fonts.length ? fonts : TEXT_FONTS_FALLBACK;
  for (const f of list.includes(cur.font) ? list : [cur.font, ...list]) fontSel.append(h("option", { value: f }, f));
  fontSel.value = cur.font;
  const sizeSel = h("select", { class: "field", id: "tx-size" });
  for (let n = 6; n <= 18; n++) sizeSel.append(h("option", { value: String(n) }, n + " pt"));
  sizeSel.value = String(cur.size_pt);
  const style = h("div", { class: "styles", role: "group", "aria-label": t("dp_text_style") });
  const tog = (key, label, css) => {
    const b = h("button", { class: "btn tog", type: "button", style: css, "aria-pressed": String(!!cur[key]), "aria-label": t("dp_text_" + key),
      title: t("dp_text_" + key) }, label);
    b.addEventListener("click", () => { cur[key] = !cur[key]; b.setAttribute("aria-pressed", String(cur[key])); });
    style.append(b);
  };
  tog("bold", "B", "font-weight:700"); tog("italic", "I", "font-style:italic"); tog("underline", "U", "text-decoration:underline");
  const colors = h("div", { class: "colors", role: "radiogroup", "aria-label": t("dp_text_color") });
  for (const c of ["white", "black"]) {
    const r = h("input", { type: "radio", name: "tx-color", id: "tx-c-" + c, checked: cur.color === c });
    r.addEventListener("change", () => { cur.color = c; });
    colors.append(h("label", { class: "chk", for: "tx-c-" + c }, r, t("dp_text_" + c)));
  }
  const reset = h("button", { class: "btn", type: "button", onclick: () => {
    Object.assign(cur, TEXT_DEFAULT);
    txt.value = ""; fontSel.value = cur.font; sizeSel.value = String(cur.size_pt);
    for (const [q, c] of Object.entries(posBoxes)) c.checked = q === cur.pos;
    for (const b of style.children) b.setAttribute("aria-pressed", "false");
    colors.querySelector("#tx-c-white").checked = true;
  } }, t("dp_text_reset"));
  const ok = h("button", { class: "btn primary", type: "button", onclick: async () => {
    ok.disabled = true;
    const value = txt.value.trim() ? { ...cur, text: txt.value.trim().slice(0, 50), font: fontSel.value, size_pt: Number(sizeSel.value) } : null;
    const done = await save(value);
    ok.disabled = false;
    if (!done) return;
    closeTextPop(host, btn);
    btn.focus();
  } }, t("dp_text_ok"));
  return h("div", { class: "textpop" + (up ? " up" : ""), role: "dialog", "aria-label": t("dp_text_title"),
    onkeydown: (e) => { if (e.key === "Escape") { e.stopPropagation(); closeTextPop(host, btn); btn.focus(); } } },
    h("div", { class: "lab" }, t("dp_text_pos")), pos,
    lab("tx-text", "dp_text_field"), txt,
    h("div", { class: "two" }, h("div", null, lab("tx-font", "dp_text_font"), fontSel), h("div", null, lab("tx-size", "dp_text_size"), sizeSel)),
    h("div", { class: "two" }, style, colors),
    h("div", { class: "popacts" }, reset, ok));
}
