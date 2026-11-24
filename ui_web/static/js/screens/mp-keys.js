// MacroPad, key binding: the pad with a click layer over it, and the inspector for the chosen key.
import bc from "../bridge.js";
import { t } from "../i18n.js";
import * as devices from "../devices.js";
import { h, clear, toast, makeScope, confirmDialog } from "../ui.js";
import { frame, statusBar, loadTypes, padPhoto, drawPad, errText, NUM_KEYS } from "./mp-common.js";

let scope = null;

export async function mount(el) {
  scope = makeScope();
  const fr = frame(el, "keys", false);
  const S = { actions: [], types: [], sel: null, draft: null, dirty: false, photo: null, pressed: null };
  const sb = statusBar(scope);
  const padHost = h("div", { style: "width:100%;display:flex;flex-direction:column;align-items:center" });
  const inspHost = h("div", { style: "width:100%" });
  fr.main.append(sb.el, padHost, inspHost);

  const act = (i) => S.actions[i] || { type: "none", action: "" };
  const assigned = (i) => !!act(i).type && act(i).type !== "none";
  const typeOf = (id) => S.types.find((x) => x.id === id);
  const typeLabel = (id) => (typeOf(id) ? typeOf(id).label : id);

  async function loadActions() {
    const r = await bc.call("macropad.actions");
    if (!scope.alive || !r.ok) return;
    S.actions = r.actions || [];
  }

  // ── the pad ──────────────────────────────────────────────────────────────
  function renderPad() {
    drawPad(padHost, {
      photo: S.photo, sel: S.sel, pressed: S.pressed, label: t("mp_grid_label"),
      key: (i) => {
        const a = act(i), on = assigned(i);
        const typ = on ? typeLabel(a.type) : t("dp_key_empty");
        return { assigned: on, text: on ? typeLabel(a.type).slice(0, 9) : "",
          aria: "M" + (i + 1) + ": " + typ + (a.action ? " " + a.action : "") };
      },
      onSelect: selectKey, onClear: clearKey });
    padHost.append(
      h("div", { class: "legend" },
        h("span", null, h("i", { style: "background:#3a3f4d" }), t("dp_legend_default")),
        h("span", null, h("i", { style: "border:2px solid #fff" }), t("dp_legend_selected")),
        h("span", null, h("i", { style: "background:var(--logo-blue)" }), t("dp_legend_assigned"))),
      h("div", { class: "gridhint" }, t("dp_grid_hint")));
  }

  async function selectKey(i) {
    if (S.sel !== i && S.dirty && !(await confirmDialog({ title: t("dp_discard_title"), message: t("dp_discard_msg"), okLabel: t("dp_discard_btn") }))) return;
    S.sel = i; S.dirty = false; S.draft = makeDraft(i);
    renderPad(); renderInspector();
  }

  async function clearKey(i) {
    if (!assigned(i)) return;
    const r = await bc.call("macropad.set_action", { index: i, type_id: "none", value: "" });
    if (!r.ok) return toast(t("ui_error", { err: r.error }), "err");
    await loadActions();
    if (S.sel === i) { S.draft = makeDraft(i); S.dirty = false; }
    renderPad(); renderInspector();
  }

  // ── inspector ────────────────────────────────────────────────────────────
  const makeDraft = (i) => ({ type: act(i).type || "none", value: act(i).action || "" });

  /** The value control for one type. Returns {el, get()}. */
  function valueEditor(typeId, value, onInput) {
    const e = typeOf(typeId) || { id: typeId, kind: "text" };
    if (typeId === "none" || e.kind === "none") {
      return { el: h("input", { class: "field plain", id: "ins-val", disabled: true, placeholder: "–", "aria-label": t("dp_insp_value") }), get: () => "" };
    }
    if (e.options && e.options.length) {
      const sel = h("select", { class: "field", id: "ins-val" }, e.options.map((o) => h("option", { value: o.value }, o.label)));
      if (value && !e.options.some((o) => o.value === value)) sel.prepend(h("option", { value }, value));
      sel.value = value || e.options[0].value;
      sel.addEventListener("change", onInput);
      return { el: sel, get: () => sel.value, initial: sel.value };
    }
    const inp = h("input", { class: "field", id: "ins-val", type: "text", value: value || "", placeholder: e.hint || "", autocomplete: "off", spellcheck: "false" });
    inp.addEventListener("input", onInput);
    if (e.browse) {
      const b = h("button", { class: "btn", type: "button", onclick: async () => {
        const p = await bc.pickFile({ title: t("dp_insp_browse"), kind: "any" });
        if (p) { inp.value = p; onInput(); }
      } }, t("dp_insp_browse"));
      return { el: h("div", { class: "inline-row" }, inp, b), get: () => inp.value };
    }
    return { el: inp, get: () => inp.value };
  }

  function renderInspector() {
    clear(inspHost);
    if (S.sel == null) {
      inspHost.append(h("div", { class: "inspector" }, h("div", { class: "head" }, h("span", { class: "acc" }), h("span", { class: "t h" }, t("dp_inspector_title"))),
        h("div", { class: "empty", style: "padding-left:0" }, t("dp_key_select"))));
      return;
    }
    const i = S.sel, d = S.draft;
    let valEd;
    const valHost = h("div");
    const typeSel = h("select", { class: "field", id: "ins-type", "aria-label": t("dp_insp_action") });
    for (const x of S.types) typeSel.append(h("option", { value: x.id }, x.label));
    const missing = !typeOf(d.type);
    if (missing) typeSel.append(h("option", { value: d.type }, d.type));
    typeSel.value = d.type;
    typeSel.addEventListener("change", () => { d.type = typeSel.value; d.value = ""; S.dirty = true; buildVal(); });
    function buildVal() {
      clear(valHost);
      valEd = valueEditor(d.type, d.value, () => { S.dirty = true; d.value = valEd.get(); });
      if (valEd.initial !== undefined && !d.value) d.value = valEd.initial;
      valHost.append(valEd.el);
    }
    buildVal();

    const apply = h("button", { class: "cta inline", type: "button", onclick: async () => {
      apply.disabled = true;
      const r = await bc.call("macropad.set_action", { index: i, type_id: d.type, value: valEd.get() });
      apply.disabled = false;
      if (!r.ok) return toast(t("mp_save_failed", { err: r.error }), "err");
      toast(t("mp_act_saved", { k: i + 1 }), "ok");
      S.dirty = false;
      await loadActions();
      S.draft = makeDraft(i);
      renderPad(); renderInspector();
    } }, t("dp_apply"));

    inspHost.append(h("div", { class: "inspector" },
      h("div", { class: "head" }, h("span", { class: "acc" }), h("h3", { class: "t h", style: "margin:0;font-weight:400" }, t("mp_key_title", { n: i + 1 })),
        h("button", { class: "btn", type: "button", onclick: () => clearKey(i) }, t("dp_clear_key"))),
      h("div", { class: "grid2" },
        h("div", null, h("label", { class: "lab", for: "ins-type" }, t("dp_insp_action")), typeSel),
        h("div", null, h("label", { class: "lab", for: "ins-val" }, t("dp_insp_value")), valHost),
        missing ? h("div", { class: "wide help" }, t("mp_type_missing", { type: d.type })) : null),
      h("div", { class: "actions" }, apply)));
  }

  // ── photo: used only when its source file has a hotspot table here ───────
  async function updatePhoto() {
    const next = await padPhoto();
    if (!scope.alive) return;
    if ((next && next.url) !== (S.photo && S.photo.url)) { S.photo = next; renderPad(); }
  }
  scope.add(devices.onPhotos(updatePhoto));

  // ── events ───────────────────────────────────────────────────────────────
  scope.on("macropad.actions_changed", async () => {
    await loadActions();
    if (S.sel != null && !S.dirty) { S.draft = makeDraft(S.sel); renderInspector(); }
    renderPad();
  });
  scope.on("macropad.key", (_t, p) => {      // a key went down on the pad: light it for a moment
    if (!p || typeof p.index !== "number") return;
    S.pressed = p.index; renderPad();
    scope.later(() => { if (S.pressed === p.index) { S.pressed = null; renderPad(); } }, 350);
  });

  // ── start ────────────────────────────────────────────────────────────────
  renderPad(); renderInspector();
  const [cat] = await Promise.all([loadTypes(), loadActions()]);
  if (!scope.alive) return;
  S.types = cat.types;
  if (!S.actions.length) S.actions = cat.actions;
  renderPad(); renderInspector();
  await updatePhoto();
}

export function unmount() { if (scope) scope.dispose(); scope = null; }
