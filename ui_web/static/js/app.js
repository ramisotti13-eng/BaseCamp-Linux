// Shell: top bar, window controls, hash router. Screens are modules exporting mount(el, ctx) / unmount().
import bc from "./bridge.js";
import * as i18n from "./i18n.js";
import { t } from "./i18n.js";
import * as devices from "./devices.js";
import * as safety from "./makalu-safety.js";
import { h, icon, clear, toast } from "./ui.js";
import * as shell from "./shell-state.js";
import { startToasts as startProfileToasts } from "./profiles-page.js";

const ROUTES = [
  { re: /^home$/, tab: null, home: true, load: () => import("./screens/home.js") },
  { re: /^displaypad\/profile$/, tab: "displaypad", load: () => import("./screens/dp-profiles.js") },
  { re: /^displaypad\/keys$/, tab: "displaypad", load: () => import("./screens/dp-keys.js") },
  { re: /^displaypad\/settings$/, tab: "displaypad", load: () => import("./screens/dp-settings.js") },
  { re: /^makalu\/profile$/, tab: "makalu67", load: () => import("./screens/mk-profiles.js") },
  { re: /^makalu\/buttons$/, tab: "makalu67", load: () => import("./screens/mk-buttons.js") },
  { re: /^makalu\/lighting$/, tab: "makalu67", load: () => import("./screens/mk-lighting.js") },
  { re: /^makalu\/macro$/, tab: "makalu67", load: () => import("./screens/mk-macros.js") },
  { re: /^makalu\/settings$/, tab: "makalu67", load: () => import("./screens/mk-settings.js") },
  // UI-3: Everest Max
  { re: /^everest_max(?:\/rgb)?$/, tab: "everest_max", load: () => import("./screens/em-rgb.js") },
  { re: /^everest_max\/profile$/, tab: "everest_max", load: () => import("./screens/em-profiles.js") },
  { re: /^everest_max\/keys$/, tab: "everest_max", load: () => import("./screens/em-keys.js") },
  { re: /^everest_max\/macro$/, tab: "everest_max", load: () => import("./screens/em-macros.js") },
  { re: /^everest_max\/display$/, tab: "everest_max", load: () => import("./screens/em-display.js") },
  { re: /^everest_max\/settings$/, tab: "everest_max", load: () => import("./screens/em-settings.js") },
  // UI-4: Everest 60 and MacroPad
  { re: /^everest60\/rgb$/, tab: "everest60", load: () => import("./screens/e60-rgb.js") },
  { re: /^everest60\/settings$/, tab: "everest60", load: () => import("./screens/e60-settings.js") },
  { re: /^macropad\/keys$/, tab: "macropad", load: () => import("./screens/mp-keys.js") },
  { re: /^macropad\/rgb$/, tab: "macropad", load: () => import("./screens/mp-rgb.js") },
  { re: /^device\/([a-z0-9_]+)$/, tab: (m) => m[1], load: () => import("./screens/placeholder.js") },
  // UI-5: the gear opens Settings; Macros, OBS and Plugins have no device and are its tabs.
  { re: /^settings$/, tab: null, gear: true, load: () => import("./screens/set-settings.js") },
  { re: /^settings\/macros$/, tab: null, gear: true, load: () => import("./screens/set-macros.js") },
  { re: /^settings\/obs$/, tab: null, gear: true, load: () => import("./screens/set-obs.js") },
  { re: /^settings\/plugins$/, tab: null, gear: true, load: () => import("./screens/set-plugins.js") },
];

const view = document.getElementById("view");
let current = null;   // {mod, route, match}
let token = 0;

function path() {
  const p = location.hash.replace(/^#\/?/, "");
  return p || "home";
}

function matchRoute(p) {
  for (const r of ROUTES) {
    const m = p.match(r.re);
    if (m) return { route: r, match: m };
  }
  return null;
}

async function navigate() {
  const my = ++token;
  const hit = matchRoute(path());
  if (!hit) { location.replace("#/home"); return; }
  if (current && current.mod.unmount) { try { current.mod.unmount(); } catch (e) { console.error(e); } }
  current = null;
  clear(view);
  let mod;
  try { mod = await hit.route.load(); } catch (e) { console.error(e); view.append(h("div", { class: "soon" }, String(e))); return; }
  if (my !== token) return;
  current = { mod, ...hit };
  renderNav();
  try {
    await mod.mount(view, { path: path(), params: hit.match.slice(1), id: hit.match[1], gear: !!hit.route.gear });
  } catch (e) {
    console.error(e);
    view.append(h("div", { class: "soon" }, h("p", null, String((e && e.message) || e))));
  }
  view.focus({ preventScroll: true });
  focusPending();
}

// The update toast opens Settings at its Update panel.
let focusId = null;
function focusPending() {
  if (!focusId) return;
  const el = document.getElementById(focusId);
  if (!el) return;
  // On a tab that is not shown (the settings' tabs): show that tab first.
  const pane = el.closest("[data-tabpanel]");
  if (pane && pane.hidden) { const b = document.querySelector(`[data-tab="${pane.dataset.tabpanel}"]`); if (b) b.click(); }
  el.scrollIntoView({ block: "start" }); focusId = null;
  // A short glow, so the eye finds the place it was sent to.
  el.classList.remove("focusglow"); void el.offsetWidth; el.classList.add("focusglow");
  setTimeout(() => el.classList.remove("focusglow"), 1600);
}
function openSettingsAt(id) {
  focusId = id;
  if (path() === "settings") focusPending();
  else location.hash = "#/settings";
}
function openUpdate() { openSettingsAt("settings-update"); }
// A screen asks for a place on the Settings screen (the photo hint on the drawings).
shell.onChange((st) => { if (st.focus) { const id = st.focus; st.focus = null; openSettingsAt(id); } });

const PAGE_ROUTES = { home: "#/home", settings: "#/settings", macros: "#/settings/macros", obs: "#/settings/obs", plugins: "#/settings/plugins" };
function pageRoute(id) {
  if (PAGE_ROUTES[id]) return PAGE_ROUTES[id];
  const d = devices.byId(id);
  return d ? d.route : null;
}

// ── top bar ────────────────────────────────────────────────────────────────

function renderNav() {
  const route = current && current.route;
  const tab = route ? (typeof route.tab === "function" ? route.tab(current.match) : route.tab) : null;
  const nav = document.getElementById("nav");
  nav.setAttribute("aria-label", t("shell_devices"));
  clear(nav);
  for (const d of devices.DEVICES) {
    if (!devices.isPresent(d.id) && d.id !== tab) continue;   // present devices only, like Base Camp
    nav.append(h("a", { href: d.route, class: d.id === tab ? "on" : "", "aria-current": d.id === tab ? "page" : null }, d.name));
  }
  const brand = document.getElementById("brand");
  brand.classList.toggle("on", !!(route && route.home));
  brand.setAttribute("aria-label", t("shell_home"));
  const gear = document.getElementById("gear");
  if (gear) gear.classList.toggle("on", !!(route && route.gear));
  renderGearBadge();
}

// The gear carries what wants attention: the plugin update count, or a dot for an update of the application.
function renderGearBadge() {
  const gear = document.getElementById("gear");
  if (!gear) return;
  const { update, plugins } = shell.state;
  gear.querySelectorAll(".bdg").forEach((b) => b.remove());
  const parts = [t("shell_settings")];
  if (update) parts.push(t("ui_update_short", { ver: update.version }));
  if (plugins) parts.push(t(plugins === 1 ? "plugin_updates_one" : "plugin_updates_many", { n: plugins }));
  gear.title = parts.join(" - ");
  gear.setAttribute("aria-label", gear.title);
  if (plugins) gear.append(h("span", { class: "bdg", "aria-hidden": "true" }, String(plugins)));
  else if (update) gear.append(h("span", { class: "bdg dot", "aria-hidden": "true" }));
}

// ── update and plugin-update notices ───────────────────────────────────────

let announcedUpdate = null;   // version the toast has been shown for
let waitingPlugins = false;   // an announcement waits for the window to come back

function announceUpdate() {
  const u = shell.state.update;
  if (!u || announcedUpdate === u.version) return;
  if (!shell.state.visible) return;   // said when the window is back
  announcedUpdate = u.version;
  toast(t("shell_update_toast", { ver: u.version }), "ok", { ms: 15000, action: { label: t("shell_update_open"), fn: openUpdate } });
}

function announcePlugins() {
  const n = shell.state.plugins;
  if (!n) { waitingPlugins = false; return; }
  if (!shell.state.visible) { waitingPlugins = true; return; }
  waitingPlugins = false;
  toast(t(n === 1 ? "plugin_updates_one" : "plugin_updates_many", { n }), "ok",
    { ms: 15000, action: { label: t("plugin_updates_show"), fn: () => { location.hash = "#/settings/plugins"; } } });
}

async function initShell() {
  shell.onChange(() => renderGearBadge());
  bc.on("update.available", (_t, p) => { if (p && p.version) { shell.set({ update: p }); announceUpdate(); } });
  bc.on("plugins.updates", (_t, p) => {
    const n = Number((p && p.count) || 0);
    shell.set({ plugins: n });
    if (p && p.announce) announcePlugins();
  });
  bc.on("ui.visible", (_t, p) => {
    const visible = !!(p && p.visible);
    shell.set({ visible });
    // A moment after the window is drawn, so the message lands on the screen (gui: 600 ms).
    if (visible) setTimeout(() => { if (waitingPlugins) announcePlugins(); announceUpdate(); }, 600);
  });
  // Pages from the control socket (`basecamp --ctl '{"cmd":"page",...}'`).
  bc.on("ui.page", (_t, p) => { const r = p && pageRoute(p.page); if (r) location.hash = r; });
  // What happened before the page was there (a start in the tray): ask for the state.
  const [sh, up, pl] = await Promise.all([bc.call("shell.state"), bc.call("update.status"), bc.call("plugins.updates")]);
  if (sh.ok) shell.set({ visible: !!sh.visible });
  if (up.ok && up.update) shell.set({ update: up.update });
  if (pl.ok) shell.set({ plugins: Number(pl.count || 0) });
  announceUpdate();
}

function renderWindowControls() {
  const win = document.getElementById("win");
  clear(win);
  const btn = (id, ic, label, fn) => h("button", { id, type: "button", "aria-label": label, title: label, onclick: fn }, icon(ic));
  win.append(
    btn("gear", "gear", t("shell_settings"), () => { location.hash = "#/settings"; }),
    btn("win-min", "min", t("shell_minimize"), () => bc.win.minimize()),
    btn("win-max", "max", t("shell_maximize"), () => bc.win.toggleMaximize()),
    btn("win-close", "close", t("shell_close"), () => bc.win.close()),
  );
}

async function start() {
  await bc.ready;
  await Promise.all([i18n.load(), devices.init(), devices.initPhotos()]);
  document.getElementById("brand").addEventListener("click", () => { location.hash = "#/home"; });
  renderWindowControls();
  startProfileToasts();   // "<name> is now the active ... profile", migration notes
  safety.start();   // the left-button confirmation can come up on any screen
  devices.onChange(() => renderNav());
  // The language changed: the strings are new, so redraw the bar and the screen.
  bc.on("i18n.changed", async () => { await i18n.load(); renderWindowControls(); renderGearBadge(); navigate(); });
  window.addEventListener("hashchange", navigate);
  await navigate();
  initShell().catch((e) => console.error("shell state", e));
}

start().catch((e) => {
  console.error(e);
  view.append(h("div", { class: "soon" }, h("p", null, String(e))));
});
