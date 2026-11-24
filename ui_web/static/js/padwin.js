// The pad window (padwin.html#<pad id>): one virtual pad in a pywebview window of its own
// (ui_web/pad_windows.py), pressed with the mouse, beside a game or on a second screen.
//
// A slim title bar (the window is frameless): the drag region with the pad's and the page's name,
// the page arrows, "always on top" and close. Below it the pad's grid (pad-grid.js), as large as
// the window lets it be; a click presses the key (vpads.press). The window follows the core:
// key, page and language changes. When the pad is gone (deleted, or a bad address) it says so
// and closes after GONE_CLOSE_MS.
//
// The window's api (PadWinApi) has call, image, ready, win_close, win_on_top and win_state,
// nothing else: no file picker, no browser, no minimise. The pin shows "always on top" as
// win_state reads it at start (a window restored on top) and as win_on_top answers. In the
// devserver (a browser tab) the window buttons do nothing.
import bc from "./bridge.js";
import * as i18n from "./i18n.js";
import { t } from "./i18n.js";
import { toast } from "./ui.js";
import { padGrid } from "./pad-grid.js";

const GONE_CLOSE_MS = 2000;

/** The pad id in the address's hash ("#p0123abcd"), or null. */
export function padIdFromHash(hash) {
  const m = /^#?(p[0-9a-f]{8})$/.exec(typeof hash === "string" ? hash : "");
  return m ? m[1] : null;
}

const $ = (id) => document.getElementById(id);
const el = {
  pw: $("pw"), name: $("pwname"), page: $("pwpage"), nav: $("pwnav"), prev: $("pwprev"), next: $("pwnext"),
  pin: $("pwpin"), close: $("pwclose"), stage: $("pwstage"), notice: $("pwnotice"),
};

const S = {
  id: padIdFromHash(location.hash),
  view: null,          // {name, rows, cols, pages, page, keys}
  grid: null,
  shape: "",
  token: 0,            // a newer load makes older answers stale
  onTop: false,
  gone: false,
};

const timers = new Map();
function soon(key, fn, ms = 80) {
  clearTimeout(timers.get(key));
  timers.set(key, setTimeout(() => { timers.delete(key); fn(); }, ms));
}

// ── the bar ─────────────────────────────────────────────────────────────────
function label(btn, text) {
  btn.setAttribute("aria-label", text);
  btn.title = text;
}

function labels() {
  label(el.prev, t("remote_prev_page"));
  label(el.next, t("remote_next_page"));
  label(el.pin, t("vp_win_on_top"));
  label(el.close, t("vp_win_close"));
  syncPin();
  if (S.gone) showNotice(t("vp_win_gone"));
}

function pageInfo(v) {
  const pages = [...v.pages].sort((a, b) => a.id - b.id);
  const i = pages.findIndex((p) => p.id === v.page);
  return { pages, i, name: i >= 0 ? pages[i].name : "" };
}

function showNotice(text) {
  el.notice.textContent = text;
  el.notice.hidden = !text;
}

// ── the pad ─────────────────────────────────────────────────────────────────
async function load() {
  if (S.gone) return;
  const token = ++S.token;
  const r = await bc.call("vpads.keys", { pad: S.id });
  if (token !== S.token || S.gone) return;
  if (!r.ok) {
    if (/no such pad/i.test(String(r.error || ""))) return gone();
    showNotice(t("vp_win_failed", { err: r.error }));
    return;
  }
  showNotice("");
  S.view = { name: r.name || "", rows: r.rows, cols: r.cols, pages: Array.isArray(r.pages) ? r.pages : [],
    page: r.current_page, keys: r.keys || {} };
  render();
}

function render() {
  const v = S.view;
  const shape = `${v.rows}x${v.cols}`;
  if (!S.grid || shape !== S.shape) {
    if (S.grid) S.grid.el.remove();
    S.shape = shape;
    S.grid = padGrid({ rows: v.rows, cols: v.cols, keys: v.keys, onPress: press });
    el.stage.append(S.grid.el);
  } else S.grid.update(v.keys);
  S.grid.el.setAttribute("aria-label", t("vp_grid_label", { name: v.name }));
  const { pages, i, name } = pageInfo(v);
  el.name.textContent = v.name;
  el.page.textContent = pages.length > 1 ? name : "";
  document.title = v.name || "BaseCamp Linux";
  el.nav.hidden = pages.length < 2;
  el.prev.disabled = i <= 0;
  el.next.disabled = i < 0 || i >= pages.length - 1;
}

/** The pad is gone: say so, then the window closes. */
function gone() {
  if (S.gone) return;
  S.gone = true;
  el.pw.classList.add("gone");
  el.nav.hidden = true;
  showNotice(t("vp_win_gone"));
  setTimeout(() => bc.win.close(), GONE_CLOSE_MS);
}

async function press(idx) {
  if (!S.id || S.gone || !S.grid) return;
  S.grid.pressed(idx);
  const r = await bc.call("vpads.press", { pad: S.id, key: idx });
  if (r.ok) return;
  if (/no such pad/i.test(String(r.error || ""))) gone();
  else toast(t("remote_press_failed", { err: r.error }), "err");
}

async function flip(step) {
  const v = S.view;
  if (!v || S.gone) return;
  const { pages, i } = pageInfo(v);
  const to = pages[i + step];
  if (!to) return;
  const r = await bc.call("vpads.switch_page", { pad: S.id, page: to.id });
  if (!r.ok) toast(t("ui_error", { err: r.error }), "err");
  soon("load", load, 30);
}

// ── the window ──────────────────────────────────────────────────────────────
/** PadWinApi's method `name`, when this page is in a pad window. */
function winApi(name) {
  const api = bc.transport === "pywebview" && window.pywebview && window.pywebview.api;
  return api && typeof api[name] === "function" ? api[name].bind(api) : null;
}

function syncPin() { el.pin.setAttribute("aria-pressed", S.onTop ? "true" : "false"); }

async function toggleOnTop() {
  const toggle = winApi("win_on_top");
  if (!toggle) return;
  try { S.onTop = !!(await toggle()); } catch (e) { console.error("always on top", e); }
  syncPin();
}

/** The window's "always on top" as it opened (restored on top from the last run). */
async function readWindowState() {
  const get = winApi("win_state");
  if (!get) return;
  try {
    const st = await get();
    if (st && typeof st === "object") { S.onTop = st.on_top === true; syncPin(); }
  } catch (e) { console.error("window state", e); }
}

el.prev.addEventListener("click", () => flip(-1));
el.next.addEventListener("click", () => flip(1));
el.pin.addEventListener("click", toggleOnTop);
el.close.addEventListener("click", () => bc.win.close());

// ── following the core ──────────────────────────────────────────────────────
const mine = (p) => p && p.pad === S.id;
bc.on("vpads.changed", (_t, p) => { if (!p || mine(p) || p.what === "list") soon("load", load); });
bc.on("vpads.page", (_t, p) => { if (mine(p)) soon("load", load, 30); });
bc.on("vpads.pressed", (_t, p) => { if (mine(p) && S.grid && !S.gone) S.grid.pressed(p.key); });
bc.on("i18n.changed", async () => { await i18n.load(); labels(); if (S.view) render(); });
// The devserver's event stream came back: events may have been missed.
window.addEventListener("bc:reconnected", () => soon("load", load));

// ── start ───────────────────────────────────────────────────────────────────
async function start() {
  labels();
  await Promise.all([i18n.load(), bc.ready.then(readWindowState)]);
  labels();
  if (!S.id) return gone();
  await load();
}

start();
