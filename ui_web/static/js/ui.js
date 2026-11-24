// Small DOM helpers: h(), icon(), toast(), dialogs, event scopes.
import bc from "./bridge.js";
import { t } from "./i18n.js";

/** h("div", {class:"x", onclick: fn, disabled: true}, child, "text", [more]) */
export function h(tag, props, ...kids) {
  const el = document.createElement(tag);
  let value;
  for (const [k, v] of Object.entries(props || {})) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "value") value = v;
    else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
    else if (v === true) el.setAttribute(k, "");
    else el.setAttribute(k, v);
  }
  append(el, kids);
  if (value !== undefined) el.value = value;
  return el;
}

export function append(el, kids) {
  for (const k of kids.flat(Infinity)) {
    if (k == null || k === false) continue;
    el.append(k instanceof Node ? k : document.createTextNode(String(k)));
  }
  return el;
}

export function clear(el) { while (el.firstChild) el.removeChild(el.firstChild); return el; }

/** An <svg><use href="#i-name"/></svg>; the symbols live in index.html. */
export function icon(name) {
  const tpl = document.createElement("template");
  tpl.innerHTML = `<svg aria-hidden="true" focusable="false"><use href="#i-${name}"/></svg>`;
  return tpl.content.firstChild;
}

export function esc(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

/** A short message over the screen. opts: {ms, action: {label, fn}} for a longer one with a button. */
export function toast(msg, kind, opts) {
  const host = document.getElementById("toasts");
  if (!host) return null;
  const action = opts && opts.action;
  const el = h("div", { class: "toast " + (kind || "") + (action ? " withact" : ""), role: kind === "err" ? "alert" : "status" }, msg);
  const close = () => el.remove();
  if (action) {
    el.append(h("button", { class: "btn", type: "button", onclick: () => { close(); action.fn(); } }, action.label));
  }
  host.append(el);
  setTimeout(close, (opts && opts.ms) || (kind === "err" ? 6000 : 3000));
  return el;
}

/** Subscriptions and timers that end together when a screen is unmounted. */
export function makeScope() {
  const offs = [];
  const timers = new Set();
  const scope = {
    alive: true,
    on(pattern, fn) { offs.push(bc.on(pattern, (topic, p) => { if (scope.alive) fn(topic, p); })); },
    later(fn, ms) {
      const id = setTimeout(() => { timers.delete(id); if (scope.alive) fn(); }, ms);
      timers.add(id);
      return id;
    },
    cancel(id) { clearTimeout(id); timers.delete(id); },
    add(off) { offs.push(off); },
    dispose() {
      scope.alive = false;
      offs.splice(0).forEach((off) => off());
      timers.forEach(clearTimeout);
      timers.clear();
    },
  };
  return scope;
}

// ── dialogs ────────────────────────────────────────────────────────────────

export function modal({ title, body, buttons, onClose }) {
  const prev = document.activeElement;
  const scrim = h("div", { class: "scrim" });
  const titleId = "dlg-title-" + Math.random().toString(36).slice(2, 7);
  const close = (result) => {
    scrim.remove();
    document.removeEventListener("keydown", onKey, true);
    if (prev && prev.focus) prev.focus();
    onClose(result);
  };
  const onKey = (e) => {
    if (e.key === "Escape") { e.stopPropagation(); close(null); }
    if (e.key === "Tab") { // keep focus inside
      const f = [...scrim.querySelectorAll("button, input, select")].filter((x) => !x.disabled);
      if (!f.length) return;
      const first = f[0], last = f[f.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    }
  };
  const dlg = h("div", { class: "dialog", role: "dialog", "aria-modal": "true", "aria-labelledby": titleId },
    h("div", { class: "head" }, h("span", { class: "acc" }), h("span", { class: "h", id: titleId, style: "font-size:17px" }, title)),
    body,
    h("div", { class: "actions" }, buttons(close)));
  scrim.append(dlg);
  scrim.addEventListener("mousedown", (e) => { if (e.target === scrim) close(null); });
  document.body.append(scrim);
  document.addEventListener("keydown", onKey, true);
  return { dlg, close };
}

/** Promise of the entered text, or null when cancelled. */
export function promptDialog({ title, label, value = "" }) {
  return new Promise((resolve) => {
    const input = h("input", { class: "field", id: "dlg-input", type: "text", value, autocomplete: "off" });
    const ok = (close) => () => { const v = input.value.trim(); if (v) close(v); };
    const { dlg } = modal({
      title,
      body: h("div", null, h("label", { class: "lab", for: "dlg-input" }, label), input),
      buttons: (close) => [
        h("button", { class: "btn", type: "button", onclick: () => close(null) }, t("ui_cancel")),
        h("button", { class: "btn primary", type: "button", onclick: ok(close) }, t("ui_ok")),
      ],
      onClose: resolve,
    });
    input.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); dlg.querySelector(".btn.primary").click(); } });
    input.focus(); input.select();
  });
}

/** Promise of true / false. `details` is an optional list of lines. */
export function confirmDialog({ title, message, details, okLabel, danger }) {
  return new Promise((resolve) => {
    const body = h("div", null, h("p", null, message),
      details && details.length ? h("ul", null, details.map((d) => h("li", null, d))) : null);
    const { dlg } = modal({
      title, body,
      buttons: (close) => [
        h("button", { class: "btn", type: "button", onclick: () => close(false) }, t("ui_cancel")),
        h("button", { class: "btn " + (danger ? "danger" : "primary"), type: "button", onclick: () => close(true) }, okLabel || t("ui_ok")),
      ],
      onClose: (r) => resolve(r === true),
    });
    dlg.querySelector(".btn:last-child").focus();
  });
}
