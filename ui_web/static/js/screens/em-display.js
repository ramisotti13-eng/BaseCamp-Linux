// Everest Max, Display Dial: a close-up of the dial and, beside it, the dial's own settings (menus,
// clock type, screensaver, menu colour, reset) as Base Camp's page has them, with Monitor Mode
// (our own feature: it feeds PC values and the clock to the dial) in a compact section below.
// Every change is written at once, read-modify-write, through everest_max.set_dock_settings.
import bc from "../bridge.js";
import { t } from "../i18n.js";
import { h, icon, clear, toast, makeScope, confirmDialog } from "../ui.js";
import { openImageEditor } from "../image-editor.js";
import { colorInput } from "../colorpicker.js";
import { createDialPicture } from "../dial-picture.js";
import { frame, statusBar, cmd } from "./em-common.js";

const METER_MS = 2000;
const DEBOUNCE_MS = 700;
// The dial's menus in the order of the original's "Menüs anzeigen" (= the bits the core writes).
const DD_MENUS = ["clock", "profile", "rgb", "volume", "brightness", "pcinfo", "apm", "custom"];
const DD_MENU_ICON = { clock: "dd-clock", profile: "dd-profile", rgb: "bulb", volume: "dd-volume", brightness: "sun", pcinfo: "monitor", apm: "dd-apm", custom: "keys" };
const DD_SECONDS = [1, 3600];
// The screensaver's content as the original lists it; "clock" is the digital or the analog code
// by the clock type (protocol.py DOCK_CONTENT).
const CONTENTS = ["image", "clock", "timer", "stopwatch", "volume", "brightness", "cpu", "gpu", "hd", "network", "ram", "apm"];
// Base Camp's defaults for everything on this page (the reset button).
const DEFAULTS = { color: "#f3cc23", screensaver: { on: true, seconds: 30 }, turn_off: { on: false, seconds: 30 } };

let scope = null;

const contentName = (v, style) => (v === "clock" ? (style === "analog" ? "clock_analog" : "clock_digital") : v);
const contentValue = (name) => (name && name.startsWith("clock_") ? "clock" : name || "");
const qmark = (text) => h("span", { class: "qm", tabindex: "0", role: "img", title: text, "aria-label": text }, "?");

export async function mount(el) {
  scope = makeScope();
  const fr = frame(el, "display", false);
  const sb = statusBar(scope);
  const tr = sb.tracker;
  const st = tr.st;
  const S = { mainPic: null };

  // ── what the keyboard has: read once, every change is one write with only that field ──────────
  const DD = { shown: null, confirmed: null, loading: false, error: "", pending: 0 };
  const ddState = h("div", { class: "help", role: "status", "aria-live": "polite" });
  const ddReload = h("button", { class: "btn", type: "button", id: "dd-reload", hidden: true, onclick: () => readDock() }, t("em_dd_reload"));
  const picHost = h("div", { class: "ddpic" });
  const pic = createDialPicture(picHost);

  const menuRows = new Map();
  const menuList = DD_MENUS.map((m) => {
    const sw = h("input", { type: "checkbox", class: "ddsw", role: "switch", "data-menu": m, disabled: true, "aria-label": t("em_dd_menu_" + m),
      onchange: () => {
        const on = sw.checked;
        writeDock({ menus: { [m]: on } }, (s) => { s.menus[m] = on; });
      } });
    menuRows.set(m, sw);
    return h("label", { class: "ddrow" }, icon(DD_MENU_ICON[m]), h("span", { class: "nm" }, t("em_dd_menu_" + m)),
      m === "custom" ? qmark(t("em_dd_custom_tip")) : null, sw);
  });

  // clock type: Digital / Analog and 12H / 24H, the clock's own settings (the style also moves what
  // the screensaver shows when that is the clock)
  function seg(label, values, current, onPick) {
    const wrap = h("div", { class: "seg ddseg", role: "group", "aria-label": label });
    for (const [v, text, ic] of values) wrap.append(h("button", { type: "button", "data-v": v, "aria-pressed": "false", onclick: () => onPick(v) }, ic ? icon(ic) : null, text));
    return { el: wrap, paint() { wrap.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.v === current()))); } };
  }
  const styleSeg = seg(t("em_dd_clock_type"), [["digital", t("em_style_digital"), "dd-digital"], ["analog", t("em_style_analog"), "dd-clock"]], () => st.style, async (v) => {
    const r = await cmd("set_style", { style: v });
    if (!r.ok) return;
    st.style = v;
    paintClock();
    const c = DD.shown && DD.shown.content;
    if (c && c.startsWith("clock_")) writeDock({ content: contentName("clock", v) }, (s) => { s.content = contentName("clock", v); });
  });
  const fmtSeg = seg(t("em_dd_clock_type"), [["12H", "12H"], ["24H", "24H"]], () => st.format, async (v) => {
    const r = await cmd("set_clock_format", { format: v });
    if (r.ok) { st.format = v; paintClock(); }
  });
  function paintClock() { styleSeg.paint(); fmtSeg.paint(); }

  // screensaver: after n seconds, off after n seconds, and what it shows
  function timerRow(key) {
    const id = "dd-" + key.replace("_", "");
    const cb = h("input", { type: "checkbox", id: id + "-on", disabled: true });
    const num = h("input", { type: "number", class: "field ddsec", id: id + "-s", min: DD_SECONDS[0], max: DD_SECONDS[1], step: 1, disabled: true,
      "aria-label": t("em_dd_" + key) + " (" + t("em_dd_seconds") + ")" });
    let timer = null;
    const commit = () => {
      clearTimeout(timer);
      const v = Math.max(DD_SECONDS[0], Math.min(DD_SECONDS[1], Math.round(Number(num.value)) || DD_SECONDS[0]));
      num.value = v;
      writeDock({ [key]: { seconds: v } }, (s) => { s[key].seconds = v; });
    };
    cb.addEventListener("change", () => { const on = cb.checked; writeDock({ [key]: { on } }, (s) => { s[key].on = on; }); });
    num.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(commit, DEBOUNCE_MS); });
    num.addEventListener("change", commit);
    scope.add(() => clearTimeout(timer));
    const el = h("div", { class: "ddtimer" }, h("label", { class: "check", for: id + "-on" }, cb, t("em_dd_" + key)), num, h("span", { class: "unit" }, t("em_dd_s")));
    return { el, paint(s) {
      cb.disabled = num.disabled = !s;
      if (!s) return;
      cb.checked = !!s[key].on;
      if (document.activeElement !== num) num.value = s[key].seconds;
    } };
  }
  const ssRow = timerRow("screensaver"), offRow = timerRow("turn_off");
  const contentSel = h("select", { class: "field", id: "dd-content", disabled: true, "aria-label": t("em_dd_content"),
    onchange: () => chooseContent(contentSel.value) },
    h("option", { value: "", hidden: true }, ""),
    CONTENTS.map((c) => h("option", { value: c }, t("em_dd_content_" + c))));

  // "Bild": our main display's picture, chosen with the image editor and sent as before
  const thumbBox = h("div", { class: "thumb ddthumb" });
  async function showMainThumb() {
    clear(thumbBox);
    if (!S.mainPic) return;
    const url = await bc.image(S.mainPic, 96);
    if (url && thumbBox.isConnected) { clear(thumbBox); thumbBox.append(h("img", { src: url, alt: "" })); }
  }
  async function pickImage() {
    const p = await openImageEditor({ target: "everest_main", current: S.mainPic });
    if (!p || !scope.alive) return;
    const r = await cmd("upload_main_image", { path: p });
    if (r.ok && r.skipped) toast(t("em_main_same"), "ok");
  }
  const imageRow = h("div", { class: "ddimage", hidden: true }, thumbBox,
    h("button", { class: "btn", type: "button", id: "dd-pick", onclick: pickImage }, t("dp_insp_pick_image")));

  async function chooseContent(v) {
    if (!v) return;
    const name = contentName(v, st.style);
    await writeDock({ content: name }, (s) => { s.content = name; });
    if (v === "image") pickImage();
  }

  const colorIn = colorInput({ id: "dd-color", class: "colorin", value: DEFAULTS.color, disabled: true, "aria-label": t("em_dd_color") });
  const colorOut = h("output", { for: "dd-color", class: "hexout" }, "");
  colorIn.addEventListener("input", () => { colorOut.textContent = colorIn.value; });
  colorIn.addEventListener("change", () => { const c = colorIn.value; writeDock({ color: c }, (s) => { s.color = c; }); });

  const resetBtn = h("button", { class: "btn danger", type: "button", id: "dd-reset", disabled: true, onclick: async () => {
    if (!(await confirmDialog({ title: t("em_dd_reset"), message: t("em_dd_reset_confirm"), okLabel: t("em_dd_reset"), danger: true }))) return;
    const clock = contentName("clock", st.style);
    const change = { menus: Object.fromEntries(DD_MENUS.map((m) => [m, true])), color: DEFAULTS.color,
      screensaver: { ...DEFAULTS.screensaver }, turn_off: { ...DEFAULTS.turn_off }, content: clock };
    const ok = await writeDock(change, (s) => {
      for (const m of DD_MENUS) s.menus[m] = true;
      s.color = DEFAULTS.color; s.screensaver = { ...DEFAULTS.screensaver }; s.turn_off = { ...DEFAULTS.turn_off }; s.content = clock;
    });
    if (ok) toast(t("em_dd_reset_done"), "ok");
  } }, t("em_dd_reset"));

  function paintDock() {
    const s = DD.shown;
    for (const [m, sw] of menuRows) { sw.disabled = !s; sw.checked = !!(s && s.menus[m]); }
    colorIn.disabled = contentSel.disabled = resetBtn.disabled = !s;
    if (s) {
      colorIn.value = s.color; colorOut.textContent = s.color;
      contentSel.value = contentValue(s.content);
    }
    imageRow.hidden = !(s && s.content === "image");
    ssRow.paint(s); offRow.paint(s);
    pic.update({ menus: s ? s.menus : {}, color: s ? s.color : DEFAULTS.color });
    ddState.textContent = DD.error ? t("em_dd_no_answer", { err: DD.error }) : DD.loading && !s ? t("em_dd_loading") : "";
    ddReload.hidden = !DD.error;
  }
  async function readDock() {
    DD.error = ""; DD.loading = true; paintDock();
    const r = await bc.call("everest_max.dock_settings");
    if (!scope.alive) return;
    if (!r.ok) { DD.loading = false; DD.error = r.error || "?"; paintDock(); return; }
    if (r.settings && !DD.pending) { DD.confirmed = r.settings; DD.shown = r.settings; }
    DD.loading = !!r.job;
    paintDock();
  }
  /** Shows the change at once and sends it; the keyboard's answer (or a failure) sets what is shown. */
  async function writeDock(change, edit) {
    if (!DD.shown) return false;
    const next = JSON.parse(JSON.stringify(DD.shown));
    edit(next);
    DD.shown = next;
    paintDock();
    DD.pending++;
    const r = await cmd("set_dock_settings", change);
    if (!r.ok) { DD.pending--; if (!DD.pending && DD.confirmed) DD.shown = DD.confirmed; paintDock(); }
    return !!r.ok;
  }
  scope.on("everest_max.dock_settings", (_t, p) => {
    if (!p || !p.settings) return;
    DD.confirmed = p.settings; DD.loading = false; DD.error = "";
    if (!DD.pending) DD.shown = p.settings;
    paintDock();
  });

  const col = (...kids) => h("div", { class: "ddcol" }, kids);
  const lab = (text, tip) => h("span", { class: "lab" }, text, tip ? qmark(tip) : null);
  const dialCard = h("section", { class: "panel ddcard", id: "dd-card" },
    h("div", { class: "head" }, h("span", { class: "acc" }), h("h2", { class: "t h", style: "margin:0;font-weight:400" }, t("em_dd_title"))),
    h("div", { class: "ddcols" },
      col(lab(t("em_dd_menus")), h("div", { class: "ddmenus" }, menuList)),
      col(
        h("div", null, lab(t("em_dd_clock_type")), h("div", { class: "ddsegs" }, styleSeg.el, fmtSeg.el)),
        h("div", null, lab(t("em_dd_screensaver_title"), t("em_dd_screensaver_tip")), ssRow.el, offRow.el,
          h("div", { class: "ddcontent" }, h("label", { class: "unit", for: "dd-content" }, t("em_dd_content")), qmark(t("em_dd_content_tip")), contentSel), imageRow),
        h("div", null, lab(t("em_dd_customize")), h("div", { class: "range-row" }, h("label", { class: "unit", for: "dd-color" }, t("em_dd_color")), colorIn, colorOut)),
        h("div", { class: "actions", style: "margin-top:0" }, resetBtn, qmark(t("em_dd_reset_tip"))))),
    h("div", { class: "actions", style: "margin-top:0;min-height:0" }, ddState, ddReload));

  // ── Monitor Mode and its meters, condensed ───────────────────────────────────────────────────
  const monState = h("div", { class: "monstate", id: "mon-state", role: "status" });
  const monBtn = h("button", { class: "btn primary", type: "button", id: "mon-toggle", onclick: async () => {
    monBtn.disabled = true;
    const r = await cmd(st.monitor.wanted ? "stop_monitor" : "start_monitor");
    monBtn.disabled = false;
    if (r.ok) { st.monitor = { ...st.monitor, ...r }; paintMonitor(); }
  } }, t("monitor_start"));
  const METERS = [["cpu", "em_m_cpu", "%"], ["ram", "em_m_ram", "%"], ["disk", "em_m_disk", "%"], ["net_mbs", "em_m_net", " MB/s"], ["volume", "em_m_volume", " %"]];
  const meterRows = new Map();
  const meters = h("div", { class: "meters ddmeters", "aria-label": t("em_meters") },
    METERS.map(([k, key]) => {
      const bar = h("i"), val = h("span", { class: "mv" }, "–");
      meterRows.set(k, { bar, val });
      return h("div", { class: "meter" }, h("span", { class: "ml" }, t(key)), val, h("div", { class: "mbar" }, bar));
    }));
  function paintMonitor() {
    const m = st.monitor;
    let text;
    if (m.active) text = t("em_mon_running");
    else if (m.wanted && m.paused) text = t("em_mon_paused");
    else if (m.wanted) text = t("em_mon_starting");
    else if (m.error) text = t("em_mon_error", { err: m.error });
    else if (m.code != null) text = t("em_mon_ended", { code: m.code });
    else text = t("em_mon_off");
    monState.textContent = text;
    monState.className = "monstate " + (m.active ? "on" : m.wanted ? "wait" : m.error || m.code != null ? "err" : "off");
    monBtn.textContent = m.wanted ? t("monitor_stop") : t("monitor_start");
    monBtn.classList.toggle("primary", !m.wanted);
  }
  const monCard = h("section", { class: "panel ddmon" },
    h("div", { class: "head" }, h("span", { class: "acc" }), h("h2", { class: "t h", style: "margin:0;font-weight:400;flex:none" }, t("monitor_title")), monState, monBtn),
    h("div", { class: "ddmonbody" }, meters),
    h("div", { class: "help clamp", title: t("em_mon_help") }, t("em_mon_help")));

  const right = h("div", { class: "ddright" }, dialCard, monCard);
  fr.main.append(sb.el, h("div", { class: "ddpage" }, h("div", { class: "ddpicbox" }, picHost), right));

  function paintMeters(r) {
    for (const [k, , unit] of METERS) {
      const row = meterRows.get(k), v = r[k];
      if (typeof v !== "number") { row.val.textContent = "–"; row.bar.style.width = "0"; continue; }
      row.val.textContent = (k === "net_mbs" ? v.toFixed(1) : Math.round(v)) + unit;
      row.bar.style.width = Math.max(0, Math.min(100, k === "net_mbs" ? v * 10 : v)) + "%";
    }
  }
  async function pollMeters() {
    const r = await bc.call("everest_max.meters");
    if (!scope.alive) return;
    if (r.ok) paintMeters(r);
    scope.later(pollMeters, METER_MS);
  }

  // ── events and start ───────────────────────────────────────────────────────
  tr.onChange(() => { paintClock(); paintMonitor(); });
  tr.onDone(async (p) => {
    if (p.kind === "main_upload") {
      if (p.ok === false) toast(t("em_main_failed", { err: p.error || "" }), "err");
      else toast(t("em_main_done"), "ok");
      await loadPicture();
    }
    if ((p.kind === "time_sync") && p.ok === false) toast(t("ui_error", { err: p.error || "" }), "err");
    if (p.kind === "dock_read" && p.ok === false) { DD.loading = false; DD.error = p.error || "?"; paintDock(); }
    if (p.kind === "dock_write") {
      DD.pending = Math.max(0, DD.pending - 1);
      if (p.ok === false) toast(t("em_dd_failed", { err: p.error || "" }), "err");
      if (!DD.pending && DD.confirmed) DD.shown = DD.confirmed;
      paintDock();
    }
  });
  async function loadPicture() {
    const r = await bc.call("everest_max.pictures");
    if (!scope.alive) return;
    S.mainPic = r.ok ? r.main : null;
    showMainThumb();
  }
  paintClock(); paintMonitor(); paintDock();
  readDock();
  loadPicture();
  pollMeters();
}

export function unmount() { if (scope) scope.dispose(); scope = null; }
