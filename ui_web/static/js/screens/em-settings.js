// Everest Max, Einstellungen, laid out like Base Camp's page: the keyboard's picture with the firmware card on the
// left; on the right Game mode (four key combinations to switch off), the indicator LEDs and the keyboard layout,
// then our own extras (time sync, clearing the D-key actions, numpad and dock modules) and the three buttons at the
// bottom. Nothing is applied by a button: a change goes to the keyboard 300 ms after the last one.
import bc from "../bridge.js";
import { t } from "../i18n.js";
import * as devices from "../devices.js";
import { photoHint } from "../photo-hint.js";
import { artFor } from "../art.js";
import { h, toast, makeScope, confirmDialog } from "../ui.js";
import { frame, statusBar, cmd } from "./em-common.js";
import { modulesPanel } from "../modules-setting.js";
import { keyboardLayoutPanel } from "../kbd-layout-setting.js";
import { panelHead, bottomButtons, quietWrite } from "../settings-kit.js";

// The page's order; the core's keys (protocol.GAME_MODE_KEYS) are the same ids.
const GAME_KEYS = ["shift_tab", "alt_f4", "win", "alt_tab"];

let scope = null;
let mountedEl = null;

export async function mount(el) {
  scope = makeScope();
  mountedEl = el;
  const fr = frame(el, "settings", true);
  const sb = statusBar(scope);

  // ── left: the keyboard and its firmware ──────────────────────────────────
  const pic = h("div", { class: "setpic", "aria-hidden": "true" });
  const tpl = document.createElement("template");
  tpl.innerHTML = artFor("everest_max").trim();          // our own static drawing, no user data
  pic.append(tpl.content.firstChild);
  devices.photoUrl("everest_max", "page", 600).then((url) => {
    if (url && scope.alive) pic.replaceChildren(h("img", { src: url, alt: "", draggable: "false" }));
  });
  const fw = { v: {}, error: "" };
  const fwNumber = h("div", { class: "fwbig", id: "fw-version" });
  const fwSub = h("div", { class: "help", role: "status", "aria-live": "polite", style: "margin:6px 0 0" });
  const paintFw = () => {
    // Base Camp shows one number: keyboard.dock.numpad (57.24.20); a module that is not attached has none.
    const parts = ["keyboard", "dock", "numpad"].map((k) => fw.v[k]).filter((x) => x != null);
    fwNumber.textContent = parts.length ? t("em_fw_version", { v: parts.join(".") }) : t("em_fw_unknown");
    fwSub.textContent = fw.error ? t("em_fw_failed", { err: fw.error }) : fw.v.busy ? t("em_fw_busy") : fw.v.bootloader != null ? t("em_fw_bootloader") + " " + fw.v.bootloader : "";
  };
  async function readVersions() {
    const r = await bc.call("everest_max.versions", {});
    if (!scope.alive) return;
    if (!r.ok) { fw.error = r.error || "?"; paintFw(); return; }
    fw.v = r; paintFw();
  }
  scope.on("everest_max.versions", (_t, p) => { fw.v = p || {}; fw.error = ""; paintFw(); });
  sb.tracker.onDone((p) => {
    if (p.kind === "versions" && p.ok === false) { if (p.busy) fw.v = { ...fw.v, busy: true }; else fw.error = p.error || "?"; paintFw(); }
  });
  // Our drawing: a hint that offers Base Camp's photo (gone once there is one; the screen is built again then).
  const hintHost = h("div", { class: "sethint" });
  const showHint = () => { const x = photoHint("everest_max", "page"); hintHost.replaceChildren(...(x ? [x] : [])); };
  showHint();
  scope.add(devices.onPhotos(showHint));
  fr.col.append(pic, hintHost,
    h("div", { class: "fwcard", id: "fw-card" }, h("div", { class: "lab" }, t("em_fw_title")), fwNumber, fwSub));
  paintFw();

  // ── game mode and the indicator LEDs ─────────────────────────────────────
  const state = { known: false };
  const gameBoxes = new Map();
  const queued = {};                                     // game mode keys changed and not yet sent
  const sendGame = quietWrite(async () => {
    const change = { ...queued };
    for (const k of Object.keys(queued)) delete queued[k];
    await cmd("set_board_settings", { game_mode: change });
  });
  const gameChecks = GAME_KEYS.map((k) => {
    const box = h("input", { type: "checkbox", id: "gm-" + k, disabled: true });
    box.addEventListener("change", () => { queued[k] = box.checked; sendGame.call(); });
    gameBoxes.set(k, box);
    return h("label", { class: "check" }, box, h("span", null, t("em_gm_" + k)));
  });
  const ledBox = h("input", { type: "checkbox", id: "led-indicators", disabled: true });
  ledBox.addEventListener("change", () => cmd("set_board_settings", { leds: ledBox.checked }));
  const boardNote = h("div", { class: "help", role: "status", "aria-live": "polite", style: "margin-top:0" });
  const paintBoard = (s) => {
    state.known = !!s;
    for (const [k, box] of gameBoxes) {
      box.disabled = !s;
      // What the person just ticked wins over an older answer still on its way.
      if (s && !(k in queued)) box.checked = !!s.game_mode[k];
    }
    ledBox.disabled = !s;
    if (s) ledBox.checked = !!s.leds;
  };
  scope.on("everest_max.board_settings", (_t, p) => { if (p && p.settings) { boardNote.textContent = ""; paintBoard(p.settings); } });
  sb.tracker.onDone((p) => {
    if (p.kind === "board_read" && p.ok === false && !state.known) boardNote.textContent = t("em_board_failed");
    if (p.kind === "board_write" && p.ok === false) toast(t("ui_error", { err: p.error || "" }), "err");
  });
  bc.call("everest_max.board_settings", {}).then((r) => { if (scope.alive && r.ok && r.settings) paintBoard(r.settings); });
  scope.add(() => sendGame.flush());

  const gamePanel = h("div", { class: "panel" }, panelHead(t("em_set_game"), t("em_set_game_help")),
    h("div", { class: "checks" }, gameChecks),
    h("div", { class: "help" }, t("em_set_game_toggle")));
  const ledPanel = h("div", { class: "panel" }, panelHead(t("em_set_leds"), t("em_set_leds_help")),
    h("div", { class: "checks" }, h("label", { class: "check" }, ledBox, h("span", null, t("em_leds_on")))), boardNote);

  // ── our own extras ───────────────────────────────────────────────────────
  const sync = h("button", { class: "btn", type: "button", id: "set-sync", onclick: async () => {
    const r = await cmd("sync_time");
    if (r.ok) toast(t("em_sync_sending"), "ok");
  } }, t("em_sync_btn"));
  const clear = h("button", { class: "btn danger", type: "button", id: "set-reset-flash", onclick: async () => {
    if (!(await confirmDialog({ title: t("em_reset_title"), message: t("em_reset_msg"), okLabel: t("em_reset_btn"), danger: true }))) return;
    const r = await cmd("reset_buttons_flash");
    if (r.ok) toast(t("em_reset_sending"), "ok");
  } }, t("em_reset_btn"));
  sb.tracker.onDone((p) => {
    if (p.kind === "time_sync") toast(p.ok === false ? t("ui_error", { err: p.error || "" }) : t("em_sync_done"), p.ok === false ? "err" : "ok");
    if (p.kind === "reset_buttons") toast(p.ok === false ? t("reset_buttons_error") + ": " + (p.error || "") : t("reset_buttons_done"), p.ok === false ? "err" : "ok");
  });
  const extras = h("div", { class: "setextras" },
    h("div", null, sync, h("div", { class: "help clamp", title: t("em_sync_help") }, t("em_sync_help"))),
    h("div", null, clear, h("div", { class: "help clamp", title: t("reset_buttons_note") }, t("em_reset_msg"))));

  // ── the buttons at the bottom ────────────────────────────────────────────
  // "Auf Werkseinstellungen zurücksetzen" is the keyboard's own factory reset (ResetFlash): one job that takes about 4 s.
  // The service then makes our files match, and the screen is built again when the job is done.
  async function resetAll() {
    await cmd("factory_reset");
  }
  sb.tracker.onDone((p) => {
    if (p.kind !== "factory_reset") return;
    if (p.ok === false) { toast(t("em_factory_failed", { err: p.error || "" }), "err"); return; }
    toast(t("em_factory_done"), "ok");
    const el = mountedEl;
    scope.dispose();
    mount(el);
  });

  fr.col.append(extras);
  fr.main.append(sb.el, h("div", { class: "cols2" },
    h("div", { class: "stack" }, gamePanel, modulesPanel(scope)),
    h("div", { class: "stack" }, ledPanel, keyboardLayoutPanel("everest_max", scope))),
  bottomButtons({ manualsLabel: t("em_manuals_btn"), resetMessage: t("em_reset_all_msg"), onReset: resetAll }));
  if (sb.tracker.st.present !== false) readVersions();     // no keyboard, nothing to ask
}

export function unmount() { if (scope) scope.dispose(); scope = null; mountedEl = null; }
