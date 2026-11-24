// Makalu, Einstellungen, laid out like Base Camp's page: the mouse picture with its ring lit on the left; on the right
// the sensor (polling rate and button response time as sliders over named stops, angle snapping and lift-off distance
// as segmented choices), the DPI scale with a draggable marker per level and the five level rows, and the three buttons
// at the bottom. Nothing is applied by a button: a change goes to the mouse 300 ms after the last one.
import bc from "../bridge.js";
import { t } from "../i18n.js";
import * as devices from "../devices.js";
import { h, toast, makeScope } from "../ui.js";
import { segmented } from "../rgbkit.js";
import { dpiScale } from "../dpi-scale.js";
import { panelHead, helpDot, bottomButtons, quietWrite, stopSlider } from "../settings-kit.js";
import { frame, loadModel, statusBar, watchResults, mouseBox, showSavedLight } from "./mk-common.js";

let scope = null;

const POLLING = [125, 250, 500, 1000];
const DEBOUNCE = [2, 4, 6, 8, 10, 12];

export async function mount(el) {
  scope = makeScope();
  const info = await loadModel();
  if (!scope.alive) return;
  const mine = scope;
  const fr = frame(el, "settings", true, info.model);
  const sb = statusBar(scope, info);
  watchResults(scope, { quiet: true });          // changes apply all the time: only failures are worth a toast

  // The mouse with its saved light, so the ring is not a dark hole.
  const mbox = mouseBox(info, { size: "setmk" });
  fr.col.append(h("div", { class: "setpic mouse" }, mbox.el));
  scope.add(devices.onPhotos(() => mbox.update()));
  mbox.update();
  showSavedLight(mbox, () => mine.alive);

  const [dr, sr] = await Promise.all([bc.call("makalu.dpi"), bc.call("makalu.sensor")]);
  if (!scope.alive) return;
  const lim = {
    min: dr.ok && dr.min != null ? dr.min : info.dpi_min, max: dr.ok && dr.max != null ? dr.max : info.dpi_max,
    step: dr.ok && dr.step ? dr.step : info.dpi_step || 50,
  };
  const defaults = dr.ok && Array.isArray(dr.defaults) ? dr.defaults : [400, 800, 1600, 3200, 6400];
  const snap = (v) => {
    v = Math.round(Number(v) / lim.step) * lim.step;
    return Math.max(lim.min, Math.min(lim.max, Number.isFinite(v) ? v : lim.min));
  };
  const D = { levels: dr.ok && Array.isArray(dr.levels) ? dr.levels.map(snap) : defaults.map(snap), active: dr.ok && dr.active != null ? dr.active : 0 };
  const fail = (r) => { if (!r.ok) toast(t("ui_error", { err: r.error }), "err"); };

  // ── sensor ──────────────────────────────────────────────────────────────
  const S = sr.ok ? sr : { polling_rate: 1000, debounce: 2, lift_off: "low", angle_snapping: false };
  const sendPoll = quietWrite(async () => fail(await bc.call("makalu.set_polling_rate", { hz: poll.get() })));
  const sendDeb = quietWrite(async () => fail(await bc.call("makalu.set_debounce", { ms: deb.get() })));
  const poll = stopSlider("mk-poll", POLLING, S.polling_rate, { fmt: (v) => v + " Hz", onInput: () => sendPoll.call(), label: t("mk_set_poll") });
  const deb = stopSlider("mk-deb", DEBOUNCE, S.debounce, { fmt: (v) => v + " ms", onInput: () => sendDeb.call(), label: t("mk_set_debounce") });
  const angle = segmented(t("mk_set_angle"), [{ value: "off", label: t("mk_set_off") }, { value: "on", label: t("mk_set_on") }],
    S.angle_snapping ? "on" : "off", async (v) => fail(await bc.call("makalu.set_angle_snapping", { on: v === "on" })));
  const lift = segmented(t("mk_set_lift"), [{ value: "low", label: t("makalu_setting_low") }, { value: "high", label: t("makalu_setting_high") }],
    S.lift_off, async (v) => fail(await bc.call("makalu.set_lift_off", { high: v })));
  const field = (title, help, control) => h("div", { class: "setfield" },
    h("div", { class: "setlab" }, h("span", { class: "lab" }, title), help), control);
  const sensorPanel = h("div", { class: "panel mkset2" }, panelHead(t("makalu_setting_header")),
    h("div", { class: "grid2" },
      field(t("mk_set_poll"), helpDot(t("mk_set_poll_help")), poll.el),
      field(t("mk_set_debounce"), helpDot(t("mk_debounce_help")), deb.el),
      field(t("mk_set_angle"), null, angle.el),
      field(t("mk_set_lift"), null, lift.el)));

  // ── DPI ──────────────────────────────────────────────────────────────────
  const inputs = [];
  const sendDpi = quietWrite(async () => fail(await bc.call("makalu.set_dpi", { levels: D.levels.map(snap), active: D.active })));
  const scale = dpiScale({
    ...lim, levels: D.levels, active: D.active,
    onInput: (i, v) => { D.levels[i] = v; inputs[i].value = v; sendDpi.call(); },
    onActive: (i) => { D.active = i; paintActive(); sendDpi.call(); },
  });
  const rows = h("div", { class: "dpilist" });
  const paintActive = () => rows.querySelectorAll(".dpirow2").forEach((r, j) => { r.classList.toggle("act", j === D.active); r.querySelector(".lvl").setAttribute("aria-pressed", String(j === D.active)); });
  D.levels.forEach((v, i) => {
    const num = h("input", { class: "field dpinum", id: "mk-dpin" + i, type: "number", min: lim.min, max: lim.max, step: lim.step, value: v, "aria-label": t("mk_set_level", { n: i + 1 }) + " dpi" });
    num.addEventListener("change", () => {
      D.levels[i] = snap(num.value); num.value = D.levels[i];
      scale.set(D.levels, D.active);
      sendDpi.call();
    });
    inputs.push(num);
    const pick = h("button", { type: "button", class: "lvl", "aria-pressed": "false", onclick: () => { D.active = i; scale.set(D.levels, D.active); paintActive(); sendDpi.call(); } },
      t("mk_set_level", { n: i + 1 }));
    rows.append(h("div", { class: "dpirow2" }, pick, num, h("span", { class: "unit" }, "dpi")));
  });
  paintActive();
  // The mouse's own answer (a read when the screen opened, the DPI button) moves the markers; a change of ours that
  // is still waiting for its 300 ms is not overwritten by an older one.
  scope.on("makalu.dpi", (_t, p) => {
    if (!p || !Array.isArray(p.levels) || sendDpi.waiting) return;
    D.levels = p.levels.map(snap); D.active = p.active != null ? p.active : D.active;
    D.levels.forEach((v, i) => { inputs[i].value = v; });
    scale.set(D.levels, D.active); paintActive();
  });
  bc.call("makalu.read_dpi");            // as the 3.x screen did: ask the mouse for its levels when the screen opens
  scope.add(() => { sendPoll.flush(); sendDeb.flush(); sendDpi.flush(); });

  const dpiPanel = h("div", { class: "panel" }, panelHead(t("mk_set_dpi"), t("mk_set_dpi_help")), scale.el, rows);

  // ── the buttons at the bottom ────────────────────────────────────────────
  async function resetAll() {
    const r = await bc.call("makalu.reset_settings");
    if (!r.ok) { toast(t("ui_error", { err: r.error }), "err"); return; }
    toast(t("set_reset_done"), "ok");
    D.levels = r.levels.map(snap); D.active = r.active;
    D.levels.forEach((v, i) => { inputs[i].value = v; });
    scale.set(D.levels, D.active); paintActive();
    poll.set(r.sensor.polling_rate); deb.set(r.sensor.debounce);
    angle.set(r.sensor.angle_snapping ? "on" : "off"); lift.set(r.sensor.lift_off);
  }

  fr.main.append(sb.el, sensorPanel, dpiPanel,
    bottomButtons({ manualsLabel: t("mk_manuals_btn"), resetMessage: t("mk_reset_all_msg"), onReset: resetAll }));
}

export function unmount() { if (scope) scope.dispose(); scope = null; }
