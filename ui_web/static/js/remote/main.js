// The phone page (remote.html): the pads of the PC, pressed from a phone in the LAN.
//
// A menu of the pads (the DisplayPad while it is connected, then every virtual pad), and a pad as a
// grid of square tiles with its page name, page arrows and a full screen button. A tap presses the
// key on the PC (displaypad.press_key, vpads.press); the tile answers at once and the phone
// vibrates where it can. Every open phone follows the PC: key and page changes, the DisplayPad's
// live frames (a clock ticks here too), the DisplayPad coming and going.
//
// Routes in the address's hash, so the phone's back button goes back to the menu:
//   (none) the menu,  #dp the DisplayPad,  #pad/<id> a virtual pad.
// /pair (with the code in the hash) and a 401 from the PC ("bc:unpaired") show pairing instead.
// The language follows the browser: the phone cannot ask the core for its strings.
import bc from "../bridge.js";
import { t, useBundled } from "../i18n.js";
import { h, clear, icon, toast } from "../ui.js";
import { padGrid, GRID_COLS } from "../pad-grid.js";
import { mountPair } from "./pair.js";

useBundled(String(navigator.language || "").toLowerCase().startsWith("de") ? "de" : "en");

const app = document.getElementById("app");
const DP_ROWS = 2, DP_COLS = 6, DP_KEYS = 12;
/** The DisplayPad's key 0..11 as a grid index (row * 8 + col), and back. */
const dpIdx = (i) => Math.floor(i / DP_COLS) * GRID_COLS + (i % DP_COLS);
const dpKey = (idx) => Math.floor(idx / GRID_COLS) * DP_COLS + (idx % GRID_COLS);
const LOCKED_MS = 4500;

const S = {
  unpaired: false,
  started: false,
  dpPresent: false,
  pads: [],
  route: null,          // {kind: "menu"} | {kind: "dp"} | {kind: "pad", id}
  cameFromMenu: false,  // opened from the menu: back goes back in the history
  token: 0,             // a newer route makes older answers stale
  view: null,           // the pad on screen: {name, rows, cols, pages, page, keys}
  grid: null,
  shape: "",
  frames: {},           // DisplayPad key 0..11 -> how many live frames it had: fetched again on a new one
};

// ── small helpers ───────────────────────────────────────────────────────────
const timers = new Map();
function soon(key, fn, ms = 80) {
  clearTimeout(timers.get(key));
  timers.set(key, setTimeout(() => { timers.delete(key); fn(); }, ms));
}
const offline = (r) => r && !r.ok && /fetch|network|load failed/i.test(String(r.error || ""));
const vibrate = () => { try { if (navigator.vibrate) navigator.vibrate(15); } catch (e) { /* not here */ } };

function iconBtn(name, label, onclick, cls = "") {
  return h("button", { class: "ib " + cls, type: "button", "aria-label": label, title: label, onclick }, icon(name));
}

// ── routing ─────────────────────────────────────────────────────────────────
function parseRoute() {
  const hash = location.hash.replace(/^#/, "");
  if (hash === "dp") return { kind: "dp" };
  const m = /^pad\/([A-Za-z0-9_-]+)$/.exec(hash);
  if (m) return { kind: "pad", id: m[1] };
  return { kind: "menu" };
}

function go(hash) {
  if (location.hash.replace(/^#/, "") === hash) route();
  else location.hash = hash;
}

function back() {
  if (history.length > 1 && S.cameFromMenu) history.back();
  else go("");
}

async function route() {
  if (S.unpaired) return;
  const r = parseRoute();
  const prev = S.route;
  S.route = r;
  S.token++;
  S.cameFromMenu = prev && prev.kind === "menu" && r.kind !== "menu";
  if (r.kind === "menu") { S.view = null; S.grid = null; S.shape = ""; await showMenu(); }
  else await openPad(true);
}

// ── the menu ────────────────────────────────────────────────────────────────
async function loadMenu() {
  const [st, vl] = await Promise.all([bc.call("displaypad.status"), bc.call("vpads.list")]);
  if (offline(st) && offline(vl)) return false;
  S.dpPresent = !!(st && st.ok && st.present);
  S.pads = vl && vl.ok && Array.isArray(vl.pads) ? vl.pads : [];
  return true;
}

function miniGrid(rows, cols) {
  const g = h("span", { class: "mini", "aria-hidden": "true", style: `--rows:${rows};--cols:${cols}` });
  for (let i = 0; i < rows * cols; i++) g.append(h("i"));
  return g;
}

function menuItem({ name, sub, rows, cols, hash }) {
  return h("li", null, h("button", { class: "paditem", type: "button", onclick: () => go(hash) },
    miniGrid(rows, cols),
    h("span", { class: "txt" }, h("span", { class: "nm h" }, name), h("span", { class: "sub" }, sub)),
    icon("next")));
}

async function showMenu() {
  const token = S.token;
  if (!app.querySelector(".menu")) renderLoading();
  const ok = await loadMenu();
  if (token !== S.token || S.unpaired) return;
  if (!ok) return renderOffline();
  const items = [];
  if (S.dpPresent) {
    items.push(menuItem({ name: t("remote_displaypad"), sub: t("remote_real_pad"), rows: DP_ROWS, cols: DP_COLS, hash: "dp" }));
  }
  for (const p of S.pads) {
    items.push(menuItem({ name: p.name || t("remote_virtual_pad"), sub: t("remote_grid", { rows: p.rows, cols: p.cols }),
      rows: p.rows, cols: p.cols, hash: "pad/" + p.id }));
  }
  clear(app);
  app.append(h("div", { class: "screen menu" },
    h("header", { class: "bar" },
      h("img", { class: "logo", src: "img/logo_mark.png", alt: "" }),
      h("h1", { class: "h" }, t("remote_pads"))),
    h("main", { class: "menulist" },
      items.length ? h("ul", { class: "pads" }, items) : h("p", { class: "empty dim" }, t("remote_no_pads")),
      h("p", { class: "hint dim" }, t("remote_home_hint")))));
}

function renderLoading() {
  clear(app);
  app.append(h("div", { class: "screen center" }, h("p", { class: "dim", role: "status" }, t("remote_loading"))));
}

function renderOffline() {
  clear(app);
  app.append(h("div", { class: "screen center" },
    h("img", { class: "pairlogo", src: "img/logo_mark.png", alt: "" }),
    h("p", { role: "alert" }, t("remote_offline")),
    h("button", { class: "btn primary big", type: "button", onclick: () => { renderLoading(); start(); } }, t("remote_retry"))));
}

// ── a pad ───────────────────────────────────────────────────────────────────
/** The pad of the current route from the PC: {name, rows, cols, pages, page, keys} or {error}.
 *  keysOnly: the DisplayPad's keys, its pages and presence as they were (a live frame). */
async function loadPad(keysOnly) {
  const r = S.route;
  if (r.kind === "dp") {
    const old = keysOnly && S.view ? S.view : null;
    const [lp, kr, st] = await Promise.all([old ? null : bc.call("displaypad.list_pages"), bc.call("displaypad.keys"),
      old ? null : bc.call("displaypad.status")]);
    if (st && st.ok) S.dpPresent = !!st.present;
    if (!kr.ok) return { error: kr.error, offline: offline(kr) };
    const keys = {};
    for (const k of Array.isArray(kr.keys) ? kr.keys : []) {
      if (!k || !(k.idx >= 0 && k.idx < DP_KEYS)) continue;
      keys[dpIdx(k.idx)] = { image: k.image || "", text: k.text || null, rev: S.frames[k.idx] || 0 };
    }
    const pages = old ? old.pages : lp && lp.ok && Array.isArray(lp.pages) ? lp.pages : [];
    return { name: t("remote_displaypad"), rows: DP_ROWS, cols: DP_COLS, pages, page: kr.page, keys };
  }
  const kr = await bc.call("vpads.keys", { pad: r.id });
  if (!kr.ok) return { error: kr.error, offline: offline(kr), gone: /no such pad/i.test(String(kr.error || "")) };
  return { name: kr.name, rows: kr.rows, cols: kr.cols, pages: Array.isArray(kr.pages) ? kr.pages : [],
    page: kr.current_page, keys: kr.keys || {} };
}

async function openPad(fresh, keysOnly) {
  const token = S.token;
  if (fresh) renderLoading();
  const pad = await loadPad(keysOnly && !fresh);
  if (token !== S.token || S.unpaired) return;
  if (pad.error) {
    if (pad.offline) return renderOffline();
    if (pad.gone) { toast(t("remote_pad_gone"), "err"); return go(""); }
    toast(t("ui_error", { err: pad.error }), "err");
    return go("");
  }
  S.view = pad;
  renderPad(fresh);
}

function pageInfo(v) {
  const pages = [...v.pages].sort((a, b) => a.id - b.id);
  const i = pages.findIndex((p) => p.id === v.page);
  return { pages, i, name: i >= 0 ? pages[i].name : "" };
}

function renderPad(fresh) {
  const v = S.view;
  const shape = `${S.route.kind}:${S.route.id || ""}:${v.rows}x${v.cols}`;
  const { pages, i, name } = pageInfo(v);
  if (fresh || shape !== S.shape || !app.querySelector(".padview")) {
    S.shape = shape;
    S.grid = padGrid({ rows: v.rows, cols: v.cols, keys: v.keys, onPress: press });
    const fsOk = !!(document.fullscreenEnabled || document.webkitFullscreenEnabled);
    clear(app);
    app.append(h("div", { class: "screen padview" },
      h("header", { class: "bar padbar" },
        iconBtn("back", t("remote_back"), back, "back"),
        h("div", { class: "ttl" }, h("div", { class: "nm h", id: "padname" }), h("div", { class: "pg dim", id: "pagename" })),
        fsOk ? iconBtn("full", t("remote_fullscreen"), toggleFullscreen, "full") : null),
      h("nav", { class: "pagenav", id: "pagenav" },
        iconBtn("prev", t("remote_prev_page"), () => flip(-1), "prev"),
        h("span", { class: "pgnum", id: "pgnum", "aria-live": "polite" }),
        iconBtn("next", t("remote_next_page"), () => flip(1), "next")),
      h("div", { class: "stage" },
        h("div", { class: "notice", id: "notice", role: "alert", hidden: true }),
        S.grid.el)));
    syncFullscreen();
  } else {
    S.grid.update(v.keys);
  }
  app.querySelector("#padname").textContent = v.name;
  app.querySelector("#pagename").textContent = name;
  app.querySelector("#pgnum").textContent = i >= 0 ? t("remote_page_of", { n: i + 1, count: pages.length }) : "";
  const nav = app.querySelector("#pagenav");
  nav.hidden = pages.length < 2;
  nav.querySelector(".prev").disabled = i <= 0;
  nav.querySelector(".next").disabled = i < 0 || i >= pages.length - 1;
  syncAbsent();
}

/** The DisplayPad view while the pad is gone: the grid dims and says so. */
function syncAbsent() {
  if (!S.route || S.route.kind !== "dp") return;
  const view = app.querySelector(".padview");
  if (!view) return;
  view.classList.toggle("absent", !S.dpPresent);
  const n = app.querySelector("#notice");
  if (!S.dpPresent) { n.textContent = t("remote_dp_absent"); n.className = "notice"; n.hidden = false; }
  else if (n.dataset.kind !== "locked") n.hidden = true;
}

let lockedTimer = null;
function showLocked() {
  const n = app.querySelector("#notice");
  if (!n) return toast(t("remote_locked"), "err");
  n.textContent = t("remote_locked");
  n.className = "notice locked";
  n.dataset.kind = "locked";
  n.hidden = false;
  clearTimeout(lockedTimer);
  lockedTimer = setTimeout(() => { delete n.dataset.kind; n.hidden = true; syncAbsent(); }, LOCKED_MS);
}

async function press(idx) {
  const r = S.route;
  if (!r || r.kind === "menu") return;
  S.grid.pressed(idx);
  vibrate();
  const reply = r.kind === "dp"
    ? await bc.call("displaypad.press_key", { key: dpKey(idx) })
    : await bc.call("vpads.press", { pad: r.id, key: idx });
  if (reply.ok || S.unpaired) return;
  if (reply.error === "locked") showLocked();
  else if (offline(reply)) toast(t("remote_offline"), "err");
  else toast(t("remote_press_failed", { err: reply.error }), "err");
}

async function flip(step) {
  const v = S.view, r = S.route;
  if (!v || !r || r.kind === "menu") return;
  const { pages, i } = pageInfo(v);
  const to = pages[i + step];
  if (!to) return;
  const reply = r.kind === "dp"
    ? await bc.call("displaypad.switch_page", { page: to.id })
    : await bc.call("vpads.switch_page", { pad: r.id, page: to.id });
  if (!reply.ok && !S.unpaired) toast(t("ui_error", { err: reply.error }), "err");
  soon("reload", () => openPad(false), 30);
}

// ── full screen ─────────────────────────────────────────────────────────────
function fullscreenEl() { return document.fullscreenElement || document.webkitFullscreenElement || null; }

async function toggleFullscreen() {
  const d = document.documentElement;
  try {
    if (fullscreenEl()) await (document.exitFullscreen || document.webkitExitFullscreen).call(document);
    else await (d.requestFullscreen || d.webkitRequestFullscreen).call(d, { navigationUI: "hide" });
  } catch (e) { /* refused by the browser */ }
}

function syncFullscreen() {
  const b = app.querySelector(".ib.full");
  if (!b) return;
  const on = !!fullscreenEl();
  const label = on ? t("remote_fullscreen_exit") : t("remote_fullscreen");
  b.replaceChildren(icon(on ? "full-exit" : "full"));
  b.setAttribute("aria-label", label);
  b.title = label;
  b.setAttribute("aria-pressed", on ? "true" : "false");
}
document.addEventListener("fullscreenchange", syncFullscreen);
document.addEventListener("webkitfullscreenchange", syncFullscreen);

// ── following the PC ────────────────────────────────────────────────────────
const onDp = () => S.route && S.route.kind === "dp";
const onPad = (id) => S.route && S.route.kind === "pad" && S.route.id === id;
const onMenu = () => S.route && S.route.kind === "menu";

bc.on("vpads.changed", (_t, p) => {
  if (onMenu()) return soon("menu", showMenu);
  if (p && onPad(p.pad)) soon("reload", () => openPad(false));
});
bc.on("vpads.page", (_t, p) => { if (p && onPad(p.pad)) soon("reload", () => openPad(false), 30); });
bc.on("vpads.pressed", (_t, p) => { if (p && onPad(p.pad) && S.grid) S.grid.pressed(p.key); });

bc.on("displaypad.key_frame", (_t, p) => {
  if (!onDp() || !p || !(p.idx >= 0 && p.idx < DP_KEYS)) return;
  S.frames[p.idx] = (S.frames[p.idx] || 0) + 1;
  soon("frame", () => openPad(false, true), 120);
});
for (const topic of ["displaypad.key_changed", "displaypad.page_switched", "displaypad.pages_changed"]) {
  bc.on(topic, () => { if (onDp()) soon("reload", () => openPad(false)); });
}
bc.on("displaypad.device_connected", () => {
  S.dpPresent = true;
  if (onMenu()) soon("menu", showMenu);
  else if (onDp()) { syncAbsent(); soon("reload", () => openPad(false)); }
});
bc.on("displaypad.device_disconnected", () => {
  S.dpPresent = false;
  if (onMenu()) soon("menu", showMenu);
  else if (onDp()) syncAbsent();
});

/** Events may have been missed (the phone slept, the stream dropped): read everything again. */
function refresh() {
  if (!S.started || S.unpaired || !S.route) return;
  if (onMenu()) soon("menu", showMenu);
  else soon("reload", () => openPad(false));
}
window.addEventListener("bc:reconnected", refresh);
document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") refresh(); });

window.addEventListener("bc:unpaired", () => {
  if (S.unpaired) return;
  S.unpaired = true;
  S.token++;
  mountPair(app, { note: S.started ? t("remote_unpaired") : "" });
});

// ── start ───────────────────────────────────────────────────────────────────
// The code after "#" in /pair#CODE; a malformed escape ("/pair#%") is taken as it is.
function pairCode(hash) {
  const raw = String(hash || "").replace(/^#/, "");
  try { return decodeURIComponent(raw); } catch (e) { return raw; }
}

async function start() {
  if (location.pathname === "/pair") {
    S.unpaired = true;
    return mountPair(app, { code: pairCode(location.hash) });
  }
  const who = await bc.call("remote.whoami");
  if (S.unpaired) return;
  if (!who.ok) return renderOffline();
  if (!S.started) window.addEventListener("hashchange", route);
  S.started = true;
  await route();
}

start();
