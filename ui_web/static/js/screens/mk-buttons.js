// Makalu, button assignment: the mouse with numbered markers, a list of the buttons, the inspector.
// The left button is special: the core asks for a confirmation (see js/makalu-safety.js).
import bc from "../bridge.js";
import { t } from "../i18n.js";
import * as devices from "../devices.js";
import { h, clear, toast, makeScope, confirmDialog } from "../ui.js";
import { frame, loadModel, statusBar, watchResults, mouseBox, showSavedLight, fnLabel, assignmentLabel } from "./mk-common.js";

let scope = null;

export async function mount(el) {
  scope = makeScope();
  const info = await loadModel();
  if (!scope.alive) return;
  const fr = frame(el, "buttons", true, info.model);
  const sb = statusBar(scope, info);
  watchResults(scope);

  const S = { buttons: info.buttons || [], assign: {}, defaults: info.remap_defaults || {}, cats: [], sniper: { min: info.dpi_min, max: info.dpi_max, default: 400 },
    sel: null, draft: null };
  const btnName = (b) => t(b.label_key);
  const fnOf = (a) => (typeof a === "string" && a.startsWith("sniper:") ? "sniper" : a);
  const catOf = (fn) => { const c = S.cats.find((x) => x.functions.includes(fn)); return c ? c.id : (S.cats[0] || {}).id; };
  const snap = (v) => {
    const step = info.dpi_step || 50, lo = S.sniper.min || info.dpi_min, hi = S.sniper.max || info.dpi_max;
    v = Math.round(Number(v) / step) * step;
    return Math.max(lo, Math.min(hi, Number.isFinite(v) ? v : S.sniper.default));
  };

  const markersFor = () => S.buttons.map((b) => ({
    id: b.id, label: t("mk_btn_title", { n: b.id, name: btnName(b) }) + ": " + assignmentLabel(S.assign[b.id]),
    cls: (S.sel === b.id ? "sel " : "") + (S.assign[b.id] !== S.defaults[b.id] ? "changed" : ""), pressed: S.sel === b.id,
    onclick: () => select(b.id),
  }));
  const mbox = mouseBox(info, { markers: markersFor() });
  const inspHost = h("div", { class: "mkinsp" });
  fr.main.append(sb.el, h("div", { class: "mkrow" }, h("div", { class: "mkpicwrap" }, mbox.el,
    h("div", { class: "legend" },
      h("span", null, h("i", { style: "background:var(--logo-blue)" }), t("mk_legend_default")),
      h("span", null, h("i", { style: "background:var(--beak)" }), t("mk_legend_changed")),
      h("span", null, h("i", { style: "border:2px solid #fff" }), t("dp_legend_selected")))), inspHost));

  // ── the list column ─────────────────────────────────────────────────────
  function renderList() {
    const col = fr.col;
    const keep = col.contains(document.activeElement) ? document.activeElement.getAttribute("data-f") : null;
    clear(col);
    col.append(h("div", { class: "head" }, h("span", { class: "acc" }), h("h2", { class: "t h", style: "margin:0;font-weight:400" }, t("mk_buttons_title"))));
    const rows = h("div", { class: "rows" });
    for (const b of S.buttons) {
      const a = S.assign[b.id];
      rows.append(h("div", { class: "row" + (S.sel === b.id ? " on" : "") },
        h("button", { class: "pick", type: "button", "data-f": "b" + b.id, "aria-pressed": S.sel === b.id ? "true" : "false", onclick: () => select(b.id) },
          h("div", { class: "tile" + (a !== S.defaults[b.id] ? " changed" : ""), "aria-hidden": "true" }, h("span", { class: "h", style: "font-size:22px" }, b.id)),
          h("div", { class: "txt" }, h("div", { class: "nm h" }, btnName(b)), h("div", { class: "sub" }, assignmentLabel(a))))));
    }
    col.append(rows, h("div", { class: "colfoot" }, h("button", { class: "btn", type: "button", onclick: resetAll }, t("mk_reset_all"))));
    if (keep) { const f = col.querySelector(`[data-f="${keep}"]`); if (f) f.focus(); }
  }

  function select(id) {
    S.sel = id;
    const a = S.assign[id] || id;
    const fn = fnOf(a);
    S.draft = { cat: catOf(fn), fn, dpi: fn === "sniper" ? Number(String(a).split(":")[1]) || S.sniper.default : S.sniper.default };
    renderAll();
  }

  // ── the inspector ───────────────────────────────────────────────────────
  function renderInspector() {
    clear(inspHost);
    if (S.sel == null) { inspHost.append(h("div", { class: "inspector" }, h("div", { class: "empty" }, t("mk_select_hint")))); return; }
    const b = S.buttons.find((x) => x.id === S.sel);
    const d = S.draft;
    const cat = h("select", { class: "field", id: "mk-cat" }, S.cats.map((c) => h("option", { value: c.id }, t("makalu_remap_cat_" + c.id))));
    cat.value = d.cat;
    const fnSel = h("select", { class: "field", id: "mk-fn" });
    const fillFns = () => {
      clear(fnSel);
      const c = S.cats.find((x) => x.id === d.cat) || S.cats[0];
      for (const f of c.functions) fnSel.append(h("option", { value: f }, fnLabel(f)));
      if (!c.functions.includes(d.fn)) d.fn = c.functions[0];
      fnSel.value = d.fn;
    };
    fillFns();
    const dpi = h("input", { class: "field", id: "mk-sniper", type: "number", min: S.sniper.min, max: S.sniper.max, step: info.dpi_step || 50, value: d.dpi });
    const dpiBox = h("div", null, h("label", { class: "lab", for: "mk-sniper" }, t("makalu_remap_sniper_dpi").replace(/:$/, "")), dpi,
      h("div", { class: "help" }, t("mk_sniper_help", { min: S.sniper.min })));
    const warn = h("div", { class: "help warnline" });
    const sync = () => {
      dpiBox.hidden = d.fn !== "sniper";
      warn.textContent = b.id === "1" && d.fn !== "left" ? t("mk_left_warn") : "";
    };
    sync();
    cat.addEventListener("change", () => { d.cat = cat.value; fillFns(); sync(); });
    fnSel.addEventListener("change", () => { d.fn = fnSel.value; sync(); });
    dpi.addEventListener("change", () => { d.dpi = snap(dpi.value); dpi.value = d.dpi; });

    const apply = async () => {
      if (d.fn === "sniper") { d.dpi = snap(dpi.value); dpi.value = d.dpi; }
      const params = { button: b.id, function: d.fn };
      if (d.fn === "sniper") params.sniper_dpi = d.dpi;
      const r = await bc.call("makalu.remap", params);
      if (!r.ok) toast(t("ui_error", { err: r.error }), "err");
    };
    inspHost.append(h("div", { class: "inspector" },
      h("div", { class: "head" }, h("span", { class: "acc" }), h("h2", { class: "t h", style: "margin:0;font-weight:400" }, t("mk_btn_title", { n: b.id, name: btnName(b) }))),
      h("div", { class: "grid2" },
        h("div", null, h("label", { class: "lab", for: "mk-cat" }, t("makalu_remap_category").replace(/:$/, "")), cat),
        h("div", null, h("label", { class: "lab", for: "mk-fn" }, t("makalu_remap_function").replace(/:$/, "")), fnSel),
        h("div", { class: "wide" }, dpiBox, warn)),
      h("div", { class: "actions" },
        h("button", { class: "btn primary", type: "button", id: "mk-apply", onclick: apply }, t("dp_apply")),
        h("button", { class: "btn", type: "button", onclick: () => select(b.id) }, t("mk_discard")))));
  }

  function renderAll() { renderList(); mbox.setMarkers(markersFor()); renderInspector(); }

  async function resetAll() {
    if (!(await confirmDialog({ title: t("mk_reset_title"), message: t("mk_reset_msg"), okLabel: t("makalu_reset_btn") }))) return;
    const r = await bc.call("makalu.reset_remap");
    if (!r.ok) toast(t("ui_error", { err: r.error }), "err");
    else toast(t("mk_reset_done"), "ok");
  }

  async function reload() {
    const r = await bc.call("makalu.assignments");
    if (!scope.alive) return;
    if (r.ok) { const { ok, ...a } = r; S.assign = a; }
    if (S.sel != null) select(S.sel);
    else renderAll();
  }

  scope.on("makalu.remap_confirmed", reload);
  scope.on("makalu.remap_reverted", reload);
  scope.on("makalu.remap_changed", reload);
  scope.on("makalu.result", (_t, p) => { if (p && p.op === "remap") reload(); });
  scope.add(devices.onPhotos(() => mbox.update()));
  showSavedLight(mbox, () => scope.alive);

  const [cat, asg] = await Promise.all([bc.call("makalu.remap_catalog"), bc.call("makalu.assignments")]);
  if (!scope.alive) return;
  if (cat.ok) {
    S.cats = cat.categories || [];
    if (Array.isArray(cat.buttons) && cat.buttons.length) S.buttons = cat.buttons;
    if (cat.defaults) S.defaults = cat.defaults;
    if (cat.sniper) S.sniper = cat.sniper;
  }
  if (asg.ok) { const { ok, ...a } = asg; S.assign = a; }
  for (const b of S.buttons) if (S.assign[b.id] == null) S.assign[b.id] = S.defaults[b.id] || b.id;
  renderAll();
  await mbox.update();
}

export function unmount() { if (scope) scope.dispose(); scope = null; }
