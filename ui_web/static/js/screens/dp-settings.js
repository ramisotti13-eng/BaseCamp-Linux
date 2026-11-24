// DisplayPad, settings: brightness, rotation, debounce, minimum frame time; energy saving
// (auto-off) and the lock screen (core/devices/displaypad/power.py).
import bc from "../bridge.js";
import { t } from "../i18n.js";
import { h, toast, makeScope, confirmDialog } from "../ui.js";
import { openImageEditor } from "../image-editor.js";
import { frame, statusBar } from "./dp-common.js";

let scope = null;

const LOCK_MODES = ["none", "clock", "picture", "logo", "off"];

export async function mount(el) {
  scope = makeScope();
  const fr = frame(el, "settings", false);
  const sb = statusBar(scope);

  const bright = h("input", { type: "range", id: "set-bright", min: 0, max: 100, step: 1 });
  const brightOut = h("output", { for: "set-bright" }, "");
  const rot = h("select", { class: "field", id: "set-rot" }, [0, 90, 180, 270].map((d) => h("option", { value: String(d) }, d + "°")));
  const deb = h("input", { class: "field", id: "set-deb", type: "number", min: 0, step: 0.05 });
  const minms = h("input", { class: "field", id: "set-min", type: "number", min: 0, step: 10 });

  const save = async (cmd, params) => {
    const r = await bc.call("displaypad." + cmd, params);
    if (!r.ok) toast(t("ui_error", { err: r.error }), "err");
    else toast(t("settings_saved"), "ok");
    return r;
  };
  const fill = (s) => {
    if (!s) return;
    if (s.brightness != null) { bright.value = s.brightness; brightOut.textContent = s.brightness + " %"; }
    if (s.rotation != null) rot.value = String(s.rotation);
    if (s.debounce != null && document.activeElement !== deb) deb.value = s.debounce;
    if (s.min_ms != null && document.activeElement !== minms) minms.value = s.min_ms;
  };

  bright.addEventListener("input", () => { brightOut.textContent = bright.value + " %"; });
  bright.addEventListener("change", () => save("set_brightness", { percent: Number(bright.value) }));
  rot.addEventListener("change", () => save("set_rotation", { degrees: Number(rot.value) }));
  deb.addEventListener("change", () => { if (deb.value !== "") save("set_debounce", { seconds: Number(deb.value) }); });
  minms.addEventListener("change", () => { if (minms.value !== "") save("set_min_frame_ms", { ms: Number(minms.value) }); });

  // ── energy saving: "turn off after hh:mm:ss without a key press" ─────────
  const aoOn = h("input", { type: "checkbox", id: "set-ao" });
  const unit = (id, max, key) => h("div", { style: "display:flex;flex-direction:column;gap:6px" },
    h("input", { class: "field", id, type: "number", min: 0, max, step: 1, style: "width:7em" }),
    h("label", { class: "lab", for: id, style: "margin:0" }, t(key)));
  const aoH = unit("set-ao-h", 99, "dp_unit_hours"), aoM = unit("set-ao-m", 59, "dp_unit_minutes"), aoS = unit("set-ao-s", 59, "dp_unit_seconds");
  const fields = [aoH, aoM, aoS].map((l) => l.querySelector("input"));
  const aoSeconds = () => fields.reduce((sum, f, i) => sum + Math.max(0, Math.floor(Number(f.value) || 0)) * [3600, 60, 1][i], 0);
  const saveAutoOff = () => {
    const seconds = aoSeconds();
    if (seconds < 1) return toast(t("dp_auto_off_zero"), "err");
    save("set_auto_off", { enabled: aoOn.checked, seconds });
  };
  aoOn.addEventListener("change", saveAutoOff);
  fields.forEach((f) => f.addEventListener("change", saveAutoOff));

  // ── lock screen and sleep ─────────────────────────────────────────────────
  const lockSel = h("select", { class: "field", id: "set-lock" }, LOCK_MODES.map((m) => h("option", { value: m }, t("dp_lock_" + m))));
  const lockHours = h("select", { class: "field", id: "set-lock-hours", "aria-label": t("dp_clock_hours") },
    h("option", { value: "24" }, t("dp_clock_24h")), h("option", { value: "12" }, t("dp_clock_12h")));
  const picName = h("span", { class: "help", style: "margin:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" }, "");
  const picBtn = h("button", { class: "btn", type: "button" }, t("dp_lock_pick"));
  const hoursRow = h("div", null, h("label", { class: "lab", for: "set-lock-hours" }, t("dp_clock_hours")), lockHours);
  const picRow = h("div", null, h("span", { class: "lab" }, t("dp_lock_picture")), h("div", { class: "inline-row", style: "align-items:center" }, picBtn, picName));
  const previewImg = h("img", { alt: t("dp_lock_preview"), style: "width:100%;max-width:480px;aspect-ratio:3/1;display:block;background:#000;border-radius:6px" });
  const previewBox = h("div", { class: "wide" }, h("span", { class: "lab" }, t("dp_lock_preview")), previewImg);
  const unwatched = h("div", { class: "help wide", hidden: true }, t("dp_lock_unwatched"));
  const P = { lock: { mode: "none", picture: null, hours: 24 }, watching: true };

  const pickPicture = async () => {
    const p = await bc.pickFile({ title: t("dp_lock_pick"), kind: "image" });
    if (!p) return false;
    const r = await save("set_lock_screen", { mode: "picture", picture: p });
    return !!r.ok;
  };
  picBtn.addEventListener("click", pickPicture);
  lockSel.addEventListener("change", async () => {
    const mode = lockSel.value;
    if (mode === "picture" && !P.lock.picture) {
      if (!(await pickPicture())) lockSel.value = P.lock.mode;    // nothing picked: as it was
      return;
    }
    const r = await save("set_lock_screen", { mode });
    if (!r.ok) lockSel.value = P.lock.mode;
  });
  lockHours.addEventListener("change", () => save("set_lock_screen", { hours: Number(lockHours.value) }));

  let previewSeq = 0;
  async function showPreview() {
    const seq = ++previewSeq;
    const r = await bc.call("displaypad.preview_lock_screen", {});
    const url = r.ok && r.path ? await bc.image(r.path, 612) : null;
    if (!scope || !scope.alive || seq !== previewSeq) return;
    previewBox.hidden = !url;
    if (url) previewImg.src = url;
  }
  const fillPower = (p) => {
    if (!p || !p.lock) return;
    Object.assign(P, p);
    const ao = p.auto_off || {};
    aoOn.checked = !!ao.enabled;
    if (!fields.includes(document.activeElement)) {
      const s = Number(ao.seconds) || 0;
      [Math.floor(s / 3600), Math.floor((s % 3600) / 60), s % 60].forEach((v, i) => { fields[i].value = v; });
    }
    lockSel.value = p.lock.mode;
    lockHours.value = String(p.lock.hours || 24);
    picName.textContent = p.lock.picture ? p.lock.picture.split(/[\\/]/).pop() : t("dp_lock_no_picture");
    picName.title = p.lock.picture || "";
    hoursRow.hidden = p.lock.mode !== "clock";
    picRow.hidden = p.lock.mode !== "picture";
    unwatched.hidden = p.lock.mode === "none" || p.watching !== false;
    showPreview();
  };

  // ── start-up picture: written to the pad's flash, shown from the next plug-in on ─────────
  const suImg = h("img", { class: "su-img", alt: t("dp_startup_preview"), hidden: true });
  const suNone = h("div", { class: "su-img su-none" }, t("dp_startup_unknown"));
  const suPick = h("button", { class: "btn", type: "button" }, t("dp_startup_pick"));
  const suLogo = h("button", { class: "btn", type: "button" }, t("dp_startup_logo"));
  const suState = h("div", { class: "help", role: "status", style: "margin:0" }, "");
  let suBusy = false;
  const suBtns = (busy) => { suBusy = busy; suPick.disabled = suLogo.disabled = busy; };
  async function writeStartup(path) {
    if (suBusy) return;
    const ok = await confirmDialog({ title: t("dp_startup_confirm_title"), message: t("dp_startup_confirm"),
      okLabel: t("dp_startup_save"), danger: true });
    if (!ok) return;
    suBtns(true);
    suState.textContent = t("dp_startup_writing");
    const r = await bc.call("displaypad.set_startup_picture", { path });
    if (!scope || !scope.alive) return;
    if (!r.ok) { suBtns(false); suState.textContent = ""; return toast(t("ui_error", { err: r.error }), "err"); }
    suShown = path;
  }
  let suShown = null;
  scope.on("displaypad.startup_picture", async (_t, p) => {
    suBtns(false);
    if (p && p.ok) {
      suState.textContent = t("dp_startup_done");
      const url = suShown ? await bc.image(suShown, 400) : null;
      if (url && scope.alive) { suImg.src = url; suImg.hidden = false; suNone.hidden = true; }
    } else {
      suState.textContent = "";
      toast(t("dp_startup_failed", { err: (p && p.error) || "?" }), "err");
    }
  });
  suPick.addEventListener("click", async () => {
    // The same editor as for the fullscreen picture: the whole panel's shape (800 x 240); only its first frame is used.
    const p = await openImageEditor({ target: "displaypad_fullscreen", title: t("dp_startup_pick") });
    if (p) writeStartup(p);
  });
  suLogo.addEventListener("click", async () => {
    const r = await bc.call("displaypad.startup_logo");
    if (!r.ok) return toast(t("ui_error", { err: r.error }), "err");
    writeStartup(r.path);
  });
  const startupPanel = h("div", { class: "panel su" },
    h("div", { class: "head" }, h("span", { class: "acc" }), h("h2", { class: "t h", style: "margin:0;font-weight:400" }, t("dp_startup_title"))),
    h("div", { class: "su-row" }, h("div", { class: "su-prev" }, suImg, suNone),
      h("div", { class: "su-side" }, suPick, suLogo)),
    suState, h("div", { class: "help su-help" }, t("dp_startup_help")));

  // Two columns (app.css .cols2): the basic settings, then energy saving and the lock screen; the three
  // panels under each other needed scrolling.
  fr.main.append(sb.el, h("div", { class: "cols2" }, h("div", { class: "stack" }, h("div", { class: "panel" },
    h("div", { class: "head" }, h("span", { class: "acc" }), h("h2", { class: "t h", style: "margin:0;font-weight:400" }, t("dp_rail_settings"))),
    h("div", { class: "grid2" },
      h("div", { class: "wide" }, h("label", { class: "lab", for: "set-bright" }, t("dp_brightness_label")), h("div", { class: "range-row" }, bright, brightOut)),
      h("div", null, h("label", { class: "lab", for: "set-rot" }, t("dp_rotation_label")), rot),
      h("div", null, h("label", { class: "lab", for: "set-deb" }, t("dp_debounce_label") + " (s)"), deb,
        h("div", { class: "help" }, t("dp_debounce_help"))),
      h("div", null, h("label", { class: "lab", for: "set-min" }, t("dp_min_ms_frame").replace(/:$/, "")), minms,
        h("div", { class: "help" }, t("dp_min_ms_help"))))), startupPanel),
  h("div", { class: "stack" }, h("div", { class: "panel" },
    h("div", { class: "head" }, h("span", { class: "acc" }), h("h2", { class: "t h", style: "margin:0;font-weight:400" }, t("dp_energy_title"))),
    h("div", { class: "grid2" },
      h("div", { class: "wide" },
        h("label", { class: "check", style: "margin:0 0 10px" }, aoOn, h("span", null, t("dp_auto_off_label"))),
        h("div", { style: "display:flex;gap:12px;flex-wrap:wrap" }, aoH, aoM, aoS),
        h("div", { class: "help" }, t("dp_auto_off_help"))))),
  h("div", { class: "panel" },
    h("div", { class: "head" }, h("span", { class: "acc" }), h("h2", { class: "t h", style: "margin:0;font-weight:400" }, t("dp_lock_title"))),
    h("div", { class: "grid2" },
      h("div", null, h("label", { class: "lab", for: "set-lock" }, t("dp_lock_label")), lockSel),
      hoursRow, picRow, unwatched, previewBox,
      h("div", { class: "help wide" }, t("dp_lock_help")))))));

  scope.on("displaypad.settings_changed", (_t, p) => fill(p));
  scope.on("displaypad.power_changed", (_t, p) => fillPower(p));
  const [r, pw] = await Promise.all([bc.call("displaypad.status"), bc.call("displaypad.power")]);
  if (!scope.alive) return;
  if (r.ok) fill(r.settings);
  if (pw.ok) fillPower(pw);
  else { hoursRow.hidden = picRow.hidden = previewBox.hidden = true; }
}

export function unmount() { if (scope) scope.dispose(); scope = null; }
