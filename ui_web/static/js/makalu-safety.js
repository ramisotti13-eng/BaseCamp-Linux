// The left-button safety: when the core says it is waiting for a confirmation (makalu.remap_pending), a modal with
// a countdown comes up on whatever screen is showing. Keep confirms, Undo reverts. The core reverts by itself when
// the time runs out; this only shows the time left. Closes on remap_confirmed / remap_reverted.
import bc from "./bridge.js";
import { t } from "./i18n.js";
import { h, toast } from "./ui.js";

let box = null;   // {scrim, text, keep, undo, prev}

function show(p) {
  const left = Number(p.seconds_left) || 0;
  if (!box) {
    const prev = document.activeElement;
    const text = h("p", { id: "mkp-text", role: "status" });
    const keep = h("button", { class: "btn primary", type: "button", id: "mkp-keep", onclick: () => answer("confirm_remap") }, t("makalu_remap_keep_btn"));
    const undo = h("button", { class: "btn", type: "button", id: "mkp-undo", onclick: () => answer("revert_remap") }, t("mk_remap_undo"));
    const dlg = h("div", { class: "dialog", role: "alertdialog", "aria-modal": "true", "aria-labelledby": "mkp-title", "aria-describedby": "mkp-text" },
      h("div", { class: "head" }, h("span", { class: "acc" }), h("span", { class: "h", id: "mkp-title", style: "font-size:17px" }, t("makalu_remap_keep_title"))),
      text,
      h("div", { class: "actions" }, undo, keep));
    const scrim = h("div", { class: "scrim" }, dlg);
    document.body.append(scrim);
    document.addEventListener("keydown", onKey, true);
    box = { scrim, text, keep, undo, prev };
    undo.focus();   // the safe default: an accidental Enter puts the old function back
  }
  const gone = left <= 0;
  box.text.textContent = gone ? t("mk_remap_reverting") : t("makalu_remap_keep_text", { n: left });
  box.keep.disabled = gone;
  box.undo.disabled = gone;
}

function close() {
  if (!box) return;
  document.removeEventListener("keydown", onKey, true);
  box.scrim.remove();
  const prev = box.prev;
  box = null;
  if (prev && prev.isConnected && prev.focus) prev.focus();
}

function onKey(e) {
  if (!box) return;
  if (e.key === "Escape") { e.stopPropagation(); e.preventDefault(); answer("revert_remap"); return; }
  if (e.key === "Tab") {   // focus stays inside
    const f = [box.undo, box.keep].filter((x) => !x.disabled);
    if (!f.length) { e.preventDefault(); return; }
    const first = f[0], last = f[f.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    else if (!f.includes(document.activeElement)) { e.preventDefault(); first.focus(); }
  }
}

async function answer(cmd) {
  const r = await bc.call("makalu." + cmd);
  if (!r.ok) toast(t("ui_error", { err: r.error }), "err");
  // The window closes on the confirmed / reverted event; when the core had nothing pending it has none to send.
  else if (r.pending === false && !r.button) close();
}

export function start() {
  bc.on("makalu.remap_pending", (_t, p) => show(p || {}));
  bc.on("makalu.remap_confirmed", () => close());
  bc.on("makalu.remap_reverted", (_t, p) => {
    close();
    if (p && p.ok === false) toast(t("ui_error", { err: t("makalu_failed") }), "err");
    else toast(t("makalu_remap_reverted"), "ok");
  });
  bc.call("makalu.status").then((r) => { if (r && r.ok && r.remap_pending) show(r.remap_pending); });
}
