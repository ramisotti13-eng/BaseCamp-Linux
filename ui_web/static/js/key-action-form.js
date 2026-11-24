// The action part of a key inspector: the type menu, the value field (or picker), the target
// page, the double press, and the key's "also on press" chain kept as it is. Moved out of the
// DisplayPad key screen (dp-keys.js) so the virtual pads (vp-pads.js) edit keys the same way.
//
//   actionForm({types, value, onChange, pages, newPage, ids}) -> {el, value()}
//
// types: the parsed displaypad.action_types (parseTypes). value: the key's action dict
// {type, action, target?, double?, actions?}. onChange() runs on every edit. pages: [{id, name}]
// for a "page" action; newPage (default true) offers "+ New page" in that menu. ids: the
// element ids of the four fields (the DisplayPad's by default).
// value() -> {type, action, target?, double: {type, action, target?}, actions?}: what the
// DisplayPad's set_action takes; a page target is the page's id (or "new"), a double press's
// page target its name; `actions` (the chain) comes back unchanged.
//
// `el` holds the four cells (type, value, double type, double value) as `display: contents`,
// so they sit in the caller's two-column grid (.grid2) as its own cells.
import bc from "./bridge.js";
import { t } from "./i18n.js";
import { h, clear } from "./ui.js";

// Used when displaypad.action_types is not there (older core): the built-in types.
export const FALLBACK_TYPES = [
  { id: "none", kind: "none" }, { id: "shell" }, { id: "url" }, { id: "folder", kind: "folder" }, { id: "app", kind: "file" },
  { id: "page", kind: "page" }, { id: "macro" }, { id: "keypress", placeholder_key: "dp_keypress_hint_short" },
  { id: "text", placeholder_key: "action_type_text_hint" }, { id: "set_key" },
];

const DEFAULT_IDS = { type: "ins-type", value: "ins-val", dtype: "ins-dtype", dvalue: "ins-dval" };

/** Tolerant parse of displaypad.action_types: an array or {types|action_types|entries: [...]}. */
export function parseTypes(r) {
  let list = r && r.ok ? (r.types || r.action_types || r.entries || r.result) : null;
  if (!Array.isArray(list) || !list.length) return FALLBACK_TYPES;
  const out = [];
  let plugin = false;
  for (const e of list) {
    if (Array.isArray(e)) { out.push({ id: e[0], label: e[1], plugin }); continue; }   // [id, label] pairs
    if (!e || typeof e !== "object") continue;
    if (e.id === "_separator" || e.separator) { plugin = true; continue; }
    out.push({ ...e, plugin: plugin || !!e.plugin });
  }
  return out.length ? out : FALLBACK_TYPES;
}

export function typeLabel(types, id) {
  const e = types.find((x) => x.id === id);
  if (e && e.label) return e.label;
  const key = (e && (e.label_key || e.labelKey)) || "action_type_" + id;
  const s = t(key);
  return s === key ? (id === "obs" ? "OBS" : id) : s;
}

/** A clock key's options: face, 24h/12h, seconds. The value is "digital,24h" or e.g.
 *  "analog,12h,seconds" (core/devices/displaypad/live.py parse_clock). Returns {el, get()}. */
function clockEditor(value, onInput, labelId) {
  const toks = new Set(String(value || "").toLowerCase().split(/[,;\s]+/).filter(Boolean));
  const face = h("select", { class: "field", id: labelId, "aria-label": t("dp_clock_face") },
    h("option", { value: "digital" }, t("dp_clock_digital")), h("option", { value: "analog" }, t("dp_clock_analog")));
  const hours = h("select", { class: "field", "aria-label": t("dp_clock_hours") },
    h("option", { value: "24h" }, t("dp_clock_24h")), h("option", { value: "12h" }, t("dp_clock_12h")));
  const secs = h("input", { type: "checkbox" });
  face.value = toks.has("analog") ? "analog" : "digital";
  hours.value = toks.has("12h") || toks.has("12") ? "12h" : "24h";
  secs.checked = toks.has("seconds") || toks.has("sec");
  const sync = () => { hours.disabled = face.value === "analog"; };   // a face has no AM/PM
  for (const c of [face, hours, secs]) c.addEventListener("change", () => { sync(); onInput(); });
  sync();
  return {
    el: h("div", { class: "inline-row", style: "align-items:center;flex-wrap:wrap" }, face, hours,
      h("label", { class: "check", style: "margin:0" }, secs, h("span", null, t("dp_clock_seconds")))),
    get: () => ({ value: [face.value, hours.value].concat(secs.checked ? ["seconds"] : []).join(",") }),
  };
}

function optionList(e) {
  return (e.options || []).map((o) => (typeof o === "object" ? { value: String(o.value ?? o.id), label: String(o.label ?? o.value ?? o.id) } : { value: String(o), label: String(o) }));
}

export function actionForm({ types, value, onChange, pages = [], newPage = true, ids = {} }) {
  types = Array.isArray(types) && types.length ? types : FALLBACK_TYPES;
  const id = { ...DEFAULT_IDS, ...ids };
  const a = value && typeof value === "object" ? value : {};
  const d0 = a.double && typeof a.double === "object" ? a.double : {};
  const chain = Array.isArray(a.actions) && a.actions.length ? a.actions.map((x) => ({ ...x })) : null;
  const touch = () => { if (onChange) onChange(); };
  // A page by its name, or by its id (what the menu hands back before the key is saved).
  const pageByName = (n) => pages.find((p) => p.name === n);
  const pageByRef = (r) => pages.find((p) => p.name === r || p.id === r);

  /** The value input for one action type. Returns {el, get()} where get() -> {value, target?}. */
  function valueEditor(typeId, val, target, onInput, labelId) {
    const e = types.find((x) => x.id === typeId) || { id: typeId };
    let kind = e.kind || e.value_kind || "text";
    if (typeId === "none" || kind === "none") {
      const i = h("input", { class: "field plain", id: labelId, disabled: true, placeholder: "–", "aria-label": t("dp_insp_value") });
      return { el: i, get: () => ({ value: "" }) };
    }
    if (kind === "page") {
      const sel = h("select", { class: "field", id: labelId });
      for (const p of pages) sel.append(h("option", { value: String(p.id) }, p.name));
      if (newPage) sel.append(h("option", { value: "new" }, t("dp_new_page")));
      // A stored target first (an id on a virtual pad, page 0 included; a name on the DisplayPad), else the value.
      const known = (r) => r !== undefined && r !== null && r !== "" && r !== "new";
      const cur = (known(target) && pageByRef(target)) || pageByRef(val);
      sel.value = cur ? String(cur.id) : newPage ? "new" : String((pages[0] || {}).id ?? "");
      sel.addEventListener("change", onInput);
      return {
        el: sel,
        get: () => {
          if (sel.value === "new") return { value: t("dp_page_name_title"), target: "new" };
          const p = pages.find((x) => String(x.id) === sel.value);
          return { value: p ? p.name : "", target: p ? p.id : "new" };
        },
      };
    }
    if (kind === "clock") return clockEditor(val, onInput, labelId);
    if (kind === "select" || (e.options && e.options.length)) {
      const opts = optionList(e);
      const sel = h("select", { class: "field", id: labelId }, opts.map((o) => h("option", { value: o.value }, o.label)));
      if (val && !opts.some((o) => o.value === val)) sel.prepend(h("option", { value: val }, val));
      sel.value = val || (opts[0] ? opts[0].value : "");
      sel.addEventListener("change", onInput);
      return { el: sel, get: () => ({ value: sel.value }) };
    }
    const ph = e.placeholder || (e.placeholder_key ? t(e.placeholder_key) : "");
    const inp = h("input", { class: "field", id: labelId, type: "text", value: val || "", placeholder: ph, autocomplete: "off", spellcheck: "false" });
    inp.addEventListener("input", onInput);
    if (["file", "folder", "path"].includes(kind)) {
      const b = h("button", { class: "btn", type: "button", onclick: async () => {
        const p = await bc.pickFile({ title: t("dp_insp_browse"), kind: "any" });
        if (p) { inp.value = p; onInput(); }
      } }, t("dp_insp_browse"));
      return { el: h("div", { class: "inline-row" }, inp, b), get: () => ({ value: inp.value }) };
    }
    return { el: inp, get: () => ({ value: inp.value }) };
  }

  function typeSelect(elId, current, onInput, labelText) {
    const sel = h("select", { class: "field", id: elId });
    const base = types.filter((x) => !x.plugin), plug = types.filter((x) => x.plugin);
    const opt = (x) => h("option", { value: x.id }, typeLabel(types, x.id));
    base.forEach((x) => sel.append(opt(x)));
    if (plug.length) sel.append(h("optgroup", { label: t("dp_plugins_group") }, plug.map(opt)));
    if (!types.some((x) => x.id === current)) sel.append(h("option", { value: current }, current));
    sel.value = current;
    sel.addEventListener("change", onInput);
    sel.setAttribute("aria-label", labelText);
    return sel;
  }

  const st = { type: a.type || "none", value: a.action || "", target: a.target,
    dtype: d0.type || "none", dvalue: d0.action || "", dtarget: d0.target };
  let valEd, dblEd;
  const valHost = h("div"), dblHost = h("div");
  const typeSel = typeSelect(id.type, st.type, () => { st.type = typeSel.value; st.value = ""; st.target = undefined; buildVal(); touch(); }, t("dp_insp_action"));
  const dtypeSel = typeSelect(id.dtype, st.dtype, () => { st.dtype = dtypeSel.value; st.dvalue = ""; st.dtarget = undefined; buildDbl(); touch(); }, t("dp_on_double_click"));
  function buildVal() {
    clear(valHost);
    valEd = valueEditor(st.type, st.value, st.target, () => { const g = valEd.get(); st.value = g.value; st.target = g.target; touch(); }, id.value);
    valHost.append(valEd.el);
  }
  function buildDbl() {
    clear(dblHost);
    // The double press's page: its stored target (an id survives a rename), else its value.
    dblEd = valueEditor(st.dtype, st.dvalue, st.dtarget ?? st.dvalue, () => { const g = dblEd.get(); st.dvalue = g.value; st.dtarget = g.target; touch(); }, id.dvalue);
    dblHost.append(dblEd.el);
  }
  buildVal(); buildDbl();

  const el = h("div", { class: "actform", style: "display:contents" },
    h("div", null, h("label", { class: "lab", for: id.type }, t("dp_insp_action")), typeSel),
    h("div", null, h("label", { class: "lab", for: id.value }, t("dp_insp_value")), valHost),
    h("div", null, h("label", { class: "lab", for: id.dtype }, t("dp_on_double_click").replace(/:$/, "")), dtypeSel),
    h("div", null, h("label", { class: "lab", for: id.dvalue }, t("dp_insp_value")), dblHost));

  function read() {
    const g = valEd.get(), dg = dblEd.get();
    const out = { type: st.type, action: g.value };
    if (st.type === "page" && g.target !== undefined) out.target = g.target;
    const dbl = { type: st.dtype, action: dg.value };
    if (st.dtype === "page") { const p = pageByName(dg.value); if (p) dbl.target = p.name; }
    out.double = st.dtype === "none" ? { type: "none", action: "" } : dbl;
    if (chain) out.actions = chain.map((x) => ({ ...x }));
    return out;
  }

  return { el, value: read };
}
