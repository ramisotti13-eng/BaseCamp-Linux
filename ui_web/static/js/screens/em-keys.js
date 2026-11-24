// Everest Max, key binding, as Base Camp's "Tastenbelegung": every key of the picture can be bound (main
// board, numpad, D1-D4, the dock's buttons and turning the dial; not Fn). BINDUNGEN lists the bound keys,
// the panel under the picture has FUNKTION with the original's catalogue and the function's own fields,
// and for D1-D4 BILD (the picture and the text over it). Every change is written at once, typed values a
// moment after the last key; the core says how each function is done (core/devices/everest_max/
// keybindings.py: the keyboard's own codes, its key macros, or F13-F24 and the application).
import bc from "../bridge.js";
import { t, currentLang } from "../i18n.js";
import { h, icon, clear, toast, makeScope, confirmDialog } from "../ui.js";
import { frame, statusBar, cmd, jobLabel, progressBar, loadKeyboardInfo } from "./em-common.js";
import { renderKeyboard, legendBar, legendOf } from "../keyboard-flat.js";
import { openImageEditor } from "../image-editor.js";
import { TEXT_FONTS_FALLBACK, textLayer, textButton } from "../key-text.js";
import { FN_ICON, kidOfCode, stateOf, listKeys } from "../keybind.js";

// A typed value (URL, path) goes to the core this long after the last key.
const TYPE_MS = 600;
// Keys whose cap shows a sign: named in words.
const GLYPH_NAME = { "⌫": "emk_key_backspace", "↵": "emk_key_enter", "⇧": "emk_key_shift", "⊞": "emk_key_win", "≡": "emk_key_menu" };
// Action types of the DisplayPad's catalogue that only make sense on a pad with pages.
const PAD_ONLY = new Set(["page", "set_key", "clock", "pc_info"]);

let scope = null;

export async function mount(el) {
  scope = makeScope();
  const fr = frame(el, "keys", true);
  fr.main.classList.add("kbmain");
  const sb = statusBar(scope);
  const tr = sb.tracker;
  const kbHost = h("div", { class: "kbf-stage" });
  const panelHost = h("div", { style: "width:100%" });
  fr.main.append(sb.el, kbHost, legendBar(), panelHost);

  const S = {
    cat: null,                         // everest_max.key_functions
    keys: {}, dkeys: {}, listener: { state: "off", needed: false }, free: 12,
    types: [],                         // displaypad.action_types: OBS and macro choices, plugins, our own types
    sel: null,                         // the selected key's number
    draft: null,                       // {kid, binding} picked but not complete yet (a combination without its key)
    layout: null, modules: { numpad: true, dock: true },
    info: new Map(),                   // key number -> {badge, name, order, iso}
    pics: [null, null, null, null], imgs: new Map(), fonts: TEXT_FONTS_FALLBACK,
  };
  let kb = null;

  // ── names ─────────────────────────────────────────────────────────────────
  /** What a key is called: its legend on the keyboard's layout, a sign in words, "Num 7" on the numpad. */
  function keyName(k, onPad, legends, german, rightAlt) {
    const text = k.label.trim();
    if (!text) return t("kbf_space");
    const word = GLYPH_NAME[text] ? t(GLYPH_NAME[text]) : null;
    if (onPad) return text === "NumLk" ? (legendOf(text, null, german, false, true).lines || [text]).join(" ") : "Num " + (word || text);
    if (word) return word;
    const lg = legends && legends[text];
    if (lg && lg[0]) return lg[0];
    const l = legendOf(text, legends, german, rightAlt, false);
    return l.glyph || (l.lines || [text]).join(" ");
  }
  function buildInfo() {
    S.info.clear();
    const L = S.layout;
    if (!L) return;
    const kbd = L.keyboard || {}, german = kbd.id ? kbd.id === "de" : currentLang() === "de";
    const core = L.keys.filter((k) => k.x < L.numpad_x), pad = L.keys.filter((k) => k.x >= L.numpad_x);
    const mid = core.length ? (Math.min(...core.map((k) => k.x)) + Math.max(...core.map((k) => k.x + k.w))) / 2 : 0;
    const rows = (ks) => [...ks].sort((a, b) => a.y - b.y || a.x - b.x);
    let n = 0;
    const add = (kid, badge, name) => { if (kid != null) S.info.set(kid, { badge, name: name || badge, order: n++ }); };
    for (const k of rows(core)) add(k.kid, keyName(k, false, kbd.legends, german, k.label === "Alt" && k.x > mid));
    for (const k of rows(pad)) add(k.kid, keyName(k, true, kbd.legends, german, false));
    for (const d of L.dkeys) add(d.kid, d.label, "SW" + (d.idx + 1) + "(" + d.label + ")");
    for (const b of (L.dock && L.dock.buttons) || []) add(b.kid, t("em_dock_name_" + b.name));
  }
  const nameOf = (kid) => (S.info.get(kid) || { name: String(kid) }).name;
  const dkeyIndex = (kid) => (S.cat ? S.cat.dkeys.indexOf(kid) : -1);
  const isDock = (kid) => !!S.cat && Object.values(S.cat.dock).includes(kid);
  const fnInfo = (fn) => (S.cat ? S.cat.functions.find((f) => f.id === fn) : null);
  const typeOf = (id) => S.types.find((x) => x.id === id);
  function fnLabel(fn) {
    if (fnInfo(fn)) return t("emk_fn_" + fn);
    const e = typeOf(fn);
    if (e && e.label) return e.label;
    const key = (e && e.label_key) || "action_type_" + fn;
    const s = t(key);
    return s === key ? fn : s;
  }
  /** True for a binding the keyboard cannot do by itself (the core's keybindings.runs_in_app). */
  const runsInApp = (b) => {
    const info = fnInfo(b.function);
    return !info || info.app || (b.function === "os" && ((S.cat.app_choices || {}).os || []).includes(b.value));
  };
  const choiceLabel = (fn, v) => (fn === "hotkey" ? String(v).toUpperCase() : t("emk_" + fn + "_" + v));
  const optionLabel = (fn, v) => { const o = ((typeOf(fn) || {}).options || []).find((x) => String(x.value) === String(v)); return o ? o.label : v; };
  const keyLabel = (kid) => (kid >= 230 && kid <= 241 ? "F" + (kid - 217) : nameOf(kid));
  function summary(b) {
    if (!b || b.function === "default") return t("emk_fn_default");
    const fn = b.function, info = fnInfo(fn), label = fnLabel(fn);
    if (info && info.choices.length) return label + ": " + choiceLabel(fn, b.value);
    if (fn === "shortcut") return b.value == null ? label : label + ": " + [...(b.mods || []).map((m) => t("emk_mod_" + m)), keyLabel(b.value)].join(" + ");
    if (fn === "macro" || fn === "obs" || (typeOf(fn) || {}).options) return label + (b.value ? ": " + optionLabel(fn, b.value) : "");
    return label + (b.value ? ": " + b.value : "");
  }

  // ── the picture ────────────────────────────────────────────────────────────
  function thumb(i, size = 128) {
    const p = S.pics[i];
    if (!p) return Promise.resolve(null);
    const k = p + "|" + size;
    if (!S.imgs.has(k)) S.imgs.set(k, bc.image(p, size));
    return S.imgs.get(k);
  }
  function renderPicture() {
    if (!kb) return;
    const states = {}, labels = {};
    for (const [kid, inf] of S.info) {
      const b = S.keys[kid];
      states[kid] = stateOf(b);
      labels[kid] = inf.name + ": " + summary(b);
    }
    kb.setBindings({ states, selected: S.sel, labels });
    kb.setDKeys([0, 1, 2, 3].map((i) => ({ idx: i, label: "D" + (i + 1), thumb: thumb(i) })));
  }
  function showDial() {
    if (!kb) return;
    const mode = t("main_mode_" + (tr.st.main_mode || "clock"));
    kb.setDialLabel(t("em_dial_label") + ": " + mode, mode);
  }
  function buildKeyboard() {
    clear(kbHost);
    kb = null;
    if (!S.layout) return;
    kb = renderKeyboard(kbHost, { model: "everest_max", layout: S.layout, modules: S.modules, mode: "binding", onBind: select });
    if (!S.modules.numpad) kbHost.append(h("div", { class: "kbf-modnote" }, t("kbf_no_numpad")));
    renderPicture();
    showDial();
  }

  // ── BINDUNGEN ────────────────────────────────────────────────────────────
  function renderList() {
    const col = fr.col;
    const keep = col.contains(document.activeElement) ? document.activeElement.getAttribute("data-kid") : null;
    clear(col);
    col.append(h("div", { class: "head" }, h("span", { class: "acc" }), h("h2", { class: "t h", style: "margin:0;font-weight:400" }, t("emk_bindings"))));
    const list = h("div", { class: "bindlist", role: "list" });
    const order = new Map([...S.info].map(([k, v]) => [k, v.order]));
    for (const kid of listKeys(S.keys, S.sel, order)) {
      const b = S.keys[kid], on = kid === S.sel;
      const row = h("div", { class: "bindrow" + (on ? " on" : ""), role: "listitem" },
        h("button", { class: "pick", type: "button", "data-kid": kid, "aria-pressed": on ? "true" : "false", onclick: () => select(kid) },
          h("span", { class: "kbadge" }, (S.info.get(kid) || { badge: String(kid) }).badge), h("span", { class: "what" }, summary(b))));
      if (on && stateOf(b)) {
        row.append(h("button", { class: "btn bin", type: "button", title: t("emk_reset"), "aria-label": t("emk_reset"),
          onclick: () => write({ function: "default" }) }, icon("trash")));
      }
      list.append(row);
    }
    col.append(list);
    if (S.listener.needed && S.listener.state === "denied") col.append(h("div", { class: "help warn", style: "padding:10px 18px" }, t("emk_listener_denied")));
    if (keep) { const e = col.querySelector(`[data-kid="${keep}"]`); if (e) e.focus(); }
  }

  // ── writing ────────────────────────────────────────────────────────────────
  let typed = null, typeTimer = null;
  /** Bind the selected key. `quiet`: the panel stays as it is (a value is being typed). */
  async function write(binding, quiet = false) {
    const kid = S.sel;
    if (kid == null) return false;
    const r = await bc.call("everest_max.set_keybinding", { key: kid, ...binding });
    if (!scope.alive) return false;
    if (!r.ok) {
      toast(r.code === "no_free_fkey" ? t("emk_no_free_fkey", { max: S.cat.fkeys }) : t("ui_error", { err: r.error }), "err");
      await loadBindings();
      renderAll();
      return false;
    }
    S.keys = r.keys || S.keys;
    S.listener = r.listener || S.listener;
    if (typeof r.free_fkeys === "number") S.free = r.free_fkeys;
    S.draft = null;
    renderPicture(); renderList();
    if (!quiet && S.sel === kid) renderPanel();
    return true;
  }
  function writeSoon(binding) {
    typed = { kid: S.sel, binding };
    if (typeTimer != null) scope.cancel(typeTimer);
    typeTimer = scope.later(flushTyped, TYPE_MS);
  }
  function flushTyped() {
    if (typeTimer != null) { scope.cancel(typeTimer); typeTimer = null; }
    const p = typed;
    typed = null;
    if (!p) return;
    if (p.kid === S.sel) write(p.binding, true);
    else bc.call("everest_max.set_keybinding", { key: p.kid, ...p.binding });
  }
  // A value typed just before the screen goes still reaches the core.
  scope.add(() => { const p = typed; typed = null; if (p) bc.call("everest_max.set_keybinding", { key: p.kid, ...p.binding }); });

  function select(kid) {
    flushTyped();
    S.sel = kid;
    S.draft = null;
    renderPicture(); renderList(); renderPanel();
  }

  // ── FUNKTION: a dropdown with icons ──────────────────────────────────────────
  function fnItems(kid) {
    const own = dkeyIndex(kid) >= 0 || isDock(kid);
    const items = S.cat.functions.map((f) => ({ id: f.id, label: t("emk_fn_" + f.id), icon: FN_ICON[f.id] }));
    const plugins = S.types.filter((x) => x.plugin && !PAD_ONLY.has(x.id));
    plugins.forEach((x, n) => items.push({ id: x.id, label: x.label || x.id, icon: FN_ICON.plugin, group: n === 0 ? t("dp_plugins_group") : null }));
    const cur = (S.keys[kid] || {}).function;
    // An action D1-D4 or a dock button had before (shell, key press, text) stays choosable on it.
    if (own && cur && !items.some((x) => x.id === cur)) items.push({ id: cur, label: fnLabel(cur), icon: FN_ICON.own });
    return items;
  }
  let closePicker = null;
  scope.add(() => { if (closePicker) closePicker(false); });
  function fnPicker(items, value, onPick) {
    const cur = items.find((x) => x.id === value) || items[0];
    const btn = h("button", { class: "field fnbtn", type: "button", id: "kb-fn", "aria-haspopup": "listbox", "aria-expanded": "false" },
      icon(cur.icon), h("span", null, cur.label));
    const box = h("div", { class: "fnpick" }, btn);
    let list = null, active = 0;
    const close = (focus) => {
      if (!list) return;
      list.remove(); list = null;
      btn.setAttribute("aria-expanded", "false");
      document.removeEventListener("mousedown", outside, true);
      closePicker = null;
      if (focus) btn.focus();
    };
    const outside = (e) => { if (!box.contains(e.target)) close(false); };
    const pick = (i) => { close(true); if (items[i].id !== value) onPick(items[i].id); };
    const mark = () => {
      list.querySelectorAll("[role=option]").forEach((o) => o.classList.toggle("active", Number(o.dataset.i) === active));
      const a = list.querySelector(`[data-i="${active}"]`);
      if (a) { list.setAttribute("aria-activedescendant", a.id); a.scrollIntoView({ block: "nearest" }); }
    };
    function open() {
      active = Math.max(0, items.indexOf(cur));
      list = h("ul", { class: "fnlist", role: "listbox", tabindex: "-1", "aria-labelledby": "kb-fn-lab" });
      items.forEach((x, i) => {
        if (x.group) list.append(h("li", { class: "grp", role: "presentation" }, x.group));
        list.append(h("li", { role: "option", id: "kb-fn-" + i, "data-i": i, "aria-selected": x.id === value ? "true" : "false",
          onmousedown: (e) => e.preventDefault(), onclick: () => pick(i) }, icon(x.icon), h("span", null, x.label)));
      });
      box.append(list);
      // Down when it fits, else up (the panel sits at the bottom of a window that does not scroll).
      const r = btn.getBoundingClientRect(), below = window.innerHeight - r.bottom - 10, above = r.top - 10;
      const want = Math.min(list.scrollHeight, 420);
      if (below < want && above > below) { list.style.bottom = "calc(100% + 4px)"; list.style.maxHeight = Math.min(want, above) + "px"; }
      else { list.style.top = "calc(100% + 4px)"; list.style.maxHeight = Math.max(120, Math.min(want, below)) + "px"; }
      btn.setAttribute("aria-expanded", "true");
      document.addEventListener("mousedown", outside, true);
      closePicker = close;
      mark();
      list.focus();
      list.addEventListener("keydown", (e) => {
        if (e.key === "ArrowDown") { e.preventDefault(); active = Math.min(items.length - 1, active + 1); mark(); }
        else if (e.key === "ArrowUp") { e.preventDefault(); active = Math.max(0, active - 1); mark(); }
        else if (e.key === "Home") { e.preventDefault(); active = 0; mark(); }
        else if (e.key === "End") { e.preventDefault(); active = items.length - 1; mark(); }
        else if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(active); }
        else if (e.key === "Escape" || e.key === "Tab") { e.preventDefault(); e.stopPropagation(); close(true); }
      });
    }
    btn.addEventListener("click", () => (list ? close(true) : open()));
    btn.addEventListener("keydown", (e) => { if ((e.key === "ArrowDown" || e.key === "ArrowUp") && !list) { e.preventDefault(); open(); } });
    return box;
  }

  // ── the function's own fields ──────────────────────────────────────────────
  const field = (id, labelKey, el) => h("div", { class: "valbox" }, h("label", { class: "lab", for: id }, t(labelKey)), el);
  function selectField(id, labelKey, opts, value, onChange) {
    const sel = h("select", { class: "field", id }, opts.map((o) => h("option", { value: o.value }, o.label)));
    if (value != null && !opts.some((o) => String(o.value) === String(value))) sel.prepend(h("option", { value }, String(value)));
    sel.value = value != null ? String(value) : (opts[0] ? String(opts[0].value) : "");
    sel.addEventListener("change", () => onChange(sel.value));
    return field(id, labelKey, sel);
  }
  function textField(id, labelKey, b, browse) {
    const inp = h("input", { class: "field", id, type: "text", value: b.value || "", autocomplete: "off", spellcheck: "false",
      placeholder: browse ? "" : "https://" });
    const send = () => writeSoon({ function: b.function, value: inp.value });
    inp.addEventListener("input", send);
    inp.addEventListener("blur", flushTyped);
    if (!browse) return field(id, labelKey, inp);
    const btn = h("button", { class: "btn", type: "button", onclick: async () => {
      const p = await bc.pickFile({ title: t("dp_insp_browse"), kind: browse });
      if (p) { inp.value = p; send(); flushTyped(); }
    } }, t("dp_insp_browse"));
    return field(id, labelKey, h("div", { class: "inline-row" }, inp, btn));
  }
  function shortcutFields(b) {
    const mods = new Set(b.mods || []), iso = S.layout && S.layout.shape === "iso";
    let key = b.function === "shortcut" ? b.value : null;
    const send = () => { if (key != null) write({ function: "shortcut", value: key, mods: [...mods] }, true); };
    const boxes = h("div", { class: "kbmods", role: "group", "aria-label": t("emk_mods") },
      (S.cat.modifiers || []).map((m) => {
        const cb = h("input", { type: "checkbox", id: "kb-mod-" + m, checked: mods.has(m) });
        cb.addEventListener("change", () => { if (cb.checked) mods.add(m); else mods.delete(m); send(); });
        return h("label", { class: "chk", for: "kb-mod-" + m }, cb, t("emk_mod_" + m));
      }));
    const kf = h("input", { class: "field keyfield", id: "kb-key", type: "text", readonly: true, value: key != null ? keyLabel(key) : "",
      placeholder: t("emk_key_ph"), autocomplete: "off" });
    kf.addEventListener("keydown", (e) => {
      if (e.key === "Tab") return;                                // focus moves on
      e.preventDefault();
      const kid = kidOfCode(e.code, iso);
      if (kid == null) return;
      key = kid;
      kf.value = keyLabel(kid);
      send();
    });
    const keyBox = field("kb-key", "emk_key", kf);
    keyBox.style.flex = "none";
    return [h("div", { class: "valbox modbox", style: "flex:none" }, h("span", { class: "lab" }, t("emk_mods")), boxes), keyBox];
  }
  function valueFields(kid, b) {
    const fn = b.function, info = fnInfo(fn);
    if (fn === "default" || fn === "disable") return [];
    if (info && info.choices.length) {
      return [selectField("kb-val", "emk_fn_" + fn, info.choices.map((c) => ({ value: c, label: choiceLabel(fn, c) })), b.value,
        (v) => write({ function: fn, value: v }))];
    }
    if (fn === "shortcut") return shortcutFields(b);
    if (fn === "url") return [textField("kb-val", "emk_url", b)];
    if (fn === "app") return [textField("kb-val", "emk_path", b, "any")];
    if (fn === "folder") return [textField("kb-val", "emk_path", b, "folder")];
    const e = typeOf(fn) || {};
    if (e.options && e.options.length) {
      return [selectField("kb-val", fn === "macro" ? "emk_fn_macro" : "dp_insp_value",
        e.options.map((o) => ({ value: String(o.value), label: o.label })), b.value, (v) => write({ function: fn, value: v }))];
    }
    if (fn === "macro") return [h("div", { class: "valbox help" }, t("emk_no_macros"))];
    const inp = h("input", { class: "field", id: "kb-val", type: "text", value: b.value || "", autocomplete: "off", spellcheck: "false" });
    inp.addEventListener("input", () => writeSoon({ function: fn, value: inp.value }));
    inp.addEventListener("blur", flushTyped);
    return [field("kb-val", "dp_insp_value", inp)];
  }
  /** What the user should know about how the binding works. */
  function notes(kid, b) {
    if (b.in_keyboard) return [t("emk_in_keyboard")];
    if (b.fkey != null) {
      const out = [t("emk_app_note", { f: "F" + (b.fkey - 217), n: S.free, max: S.cat.fkeys })];
      if (S.listener.state === "denied") out.push(t("emk_listener_denied"));
      return out;
    }
    if (b.function === "default" || !runsInApp(b)) return [];
    if (dkeyIndex(kid) >= 0) return [t("emk_dkey_app_note")];
    return isDock(kid) ? [t("emk_dock_app_note")] : [];
  }

  // ── BILD (D1-D4) ────────────────────────────────────────────────────────────
  let onTrack = null;                  // the panel's upload progress follows the jobs
  const uploading = (i) => [...tr.st.jobs.values()].find((j) => j.kind === "dbutton_upload" && j.button === i);
  async function confirmFlash(i) {
    return confirmDialog({ title: t("em_upload_title", { d: i + 1 }), message: t("em_upload_msg", { d: i + 1 }), okLabel: t("em_upload_btn", { d: i + 1 }) });
  }
  function pictureBox(i) {
    const img = h("button", { class: "thumb", type: "button", title: t("emk_pick_picture", { d: i + 1 }), "aria-label": t("emk_pick_picture", { d: i + 1 }),
      onclick: async () => {
        const path = await openImageEditor({ target: "everest_dkey" });
        if (!path || !(await confirmFlash(i))) return;
        await cmd("upload_dbutton", { button: i, path });
      } });
    const fill = async () => {
      const url = await thumb(i, 112);
      if (!img.isConnected) return;
      clear(img);
      if (url) img.append(h("img", { src: url, alt: "" }));
      const lay = textLayer((S.dkeys[i] || {}).text);
      if (lay) img.append(lay);
    };
    const tHost = textButton({ text: () => (S.dkeys[i] || {}).text || null, fonts: () => S.fonts, up: true, save: async (text) => {
      if (!(await confirmFlash(i))) return false;
      const r = await cmd("set_dkey_text", { button: i, text });
      if (!r.ok) return false;
      S.dkeys[i] = { text: r.text || null };
      fill();
      return true;
    } });
    const prog = h("div", { class: "uploadstate", "aria-live": "polite" });
    const showProgress = () => {
      clear(prog);
      const j = uploading(i);
      if (j) prog.append(h("div", { class: "help" }, jobLabel(j) + " " + Math.round((Date.now() - j.t0) / 1000) + " s" + (typeof j.pct === "number" ? " · " + j.pct + " %" : "")), progressBar(j));
    };
    onTrack = () => { if (prog.isConnected) showProgress(); };
    fill(); showProgress();
    return h("div", null, h("span", { class: "lab" }, t("dp_insp_image")),
      h("div", { class: "kbpic" }, img, h("div", null, tHost)), prog);
  }

  // ── the panel ──────────────────────────────────────────────────────────────
  function renderPanel() {
    const focused = panelHost.contains(document.activeElement) ? document.activeElement.id : null;
    if (closePicker) closePicker(false);
    clear(panelHost);
    onTrack = null;
    const kid = S.sel;
    if (kid == null || !S.cat) {
      panelHost.append(h("div", { class: "inspector" }, h("div", { class: "head" }, h("span", { class: "acc" }), h("span", { class: "t h" }, t("emk_fn_label"))),
        h("div", { class: "empty", style: "padding-left:0" }, t("emk_select"))));
      return;
    }
    const b = (S.draft && S.draft.kid === kid ? S.draft.binding : null) || S.keys[kid] || { function: "default" };
    const inf = S.info.get(kid) || { badge: String(kid), name: String(kid) };
    const d = dkeyIndex(kid);
    const head = h("div", { class: "head kbhead" }, h("span", { class: "acc" }), h("span", { class: "kbadge" }, inf.badge), h("h3", { class: "kname", style: "margin:0;font-weight:400" }, inf.name));
    const fnBox = h("div", { class: "fnbox" }, h("span", { class: "lab", id: "kb-fn-lab" }, t("emk_fn_label")),
      fnPicker(fnItems(kid), b.function, (fn) => choose(kid, fn)));
    fnBox.querySelector(".fnbtn").setAttribute("aria-labelledby", "kb-fn-lab kb-fn");
    const fields = h("div", { class: "kbfields" }, fnBox, valueFields(kid, b), notes(kid, b).map((s) => h("div", { class: "help" }, s)));
    panelHost.append(h("div", { class: "inspector" }, head,
      h("div", { class: "kbgrid" }, fields, d >= 0 ? pictureBox(d) : null)));
    const again = focused && panelHost.querySelector("#" + focused);
    if (again) again.focus();
  }
  /** A function picked in FUNKTION: written at once with its first choice (as the original shows it). */
  function choose(kid, fn) {
    const info = fnInfo(fn), e = typeOf(fn) || {};
    if (info && info.choices.length) return write({ function: fn, value: info.choices[0] });
    if (fn === "shortcut") {
      // Written once a key is pressed in the key field.
      S.draft = { kid, binding: { function: "shortcut", mods: [] } };
      renderPanel();
      const f = panelHost.querySelector("#kb-key"); if (f) f.focus();
      return null;
    }
    if (e.options && e.options.length) return write({ function: fn, value: String(e.options[0].value) });
    // A URL, a path or a macro to be picked: written with the first character typed (an empty one
    // would do nothing, and on a main key it would hold an F-key).
    if (["url", "app", "folder", "macro"].includes(fn)) {
      S.draft = { kid, binding: { function: fn, value: "" } };
      renderPanel();
      const f = panelHost.querySelector("#kb-val"); if (f) f.focus();
      return null;
    }
    return write({ function: fn, value: "" });
  }

  function renderAll() { renderPicture(); renderList(); renderPanel(); }

  // ── loading and events ────────────────────────────────────────────────────
  async function loadBindings() {
    const r = await bc.call("everest_max.keybindings");
    if (!scope.alive || !r.ok) return;
    S.keys = r.keys || {}; S.dkeys = r.dkeys || {}; S.listener = r.listener || S.listener;
    if (typeof r.free_fkeys === "number") S.free = r.free_fkeys;
  }
  async function loadPictures() {
    const r = await bc.call("everest_max.pictures");
    if (!scope.alive || !r.ok) return;
    S.pics = r.dbuttons || S.pics;
    S.imgs.clear();
  }
  // A change made elsewhere (the control socket, a macro edited); the panel only when nobody is typing in it.
  scope.on("everest_max.keybindings_changed", async () => {
    const before = (S.keys[S.sel] || {}).function;
    await loadBindings();
    if (!scope.alive) return;
    renderPicture(); renderList();
    if (!typed && !panelHost.contains(document.activeElement) && (S.keys[S.sel] || {}).function !== before) renderPanel();
  });
  scope.on("everest_max.buttons_changed", () => scope.later(() => loadBindings().then(() => { if (scope.alive) { renderPicture(); renderList(); } }), 0));
  scope.on("everest_max.dock_buttons_changed", () => scope.later(() => loadBindings().then(() => { if (scope.alive) { renderPicture(); renderList(); } }), 0));
  tr.onChange(() => { showDial(); if (onTrack) onTrack(); });
  tr.onDone(async (p) => {
    if (p.kind === "keymap" && p.ok === false) toast(t("emk_write_failed", { err: p.error || "" }), "err");
    if (p.kind !== "dbutton_upload") return;
    if (p.ok === false) toast(t("em_upload_failed", { d: (p.button ?? 0) + 1, err: p.error || "" }), "err");
    else toast(p.skipped ? t("em_upload_same", { d: (p.button ?? 0) + 1 }) : t("em_upload_done", { d: (p.button ?? 0) + 1 }), "ok");
    await loadPictures();
    if (!scope.alive) return;
    renderPicture();
    if (dkeyIndex(S.sel) >= 0 && !typed) renderPanel();
  });

  // ── start ─────────────────────────────────────────────────────────────────
  renderList(); renderPanel();
  const [cat, , tyr, ki, , fo] = await Promise.all([bc.call("everest_max.key_functions"), loadBindings(), bc.call("displaypad.action_types"),
    loadKeyboardInfo(), loadPictures(), bc.call("displaypad.fonts")]);
  if (!scope.alive) return;
  if (cat && cat.ok) S.cat = cat;
  if (tyr && tyr.ok && Array.isArray(tyr.types)) S.types = tyr.types.filter((x) => x && x.id);
  if (fo && fo.ok && Array.isArray(fo.fonts) && fo.fonts.length) S.fonts = fo.fonts;
  S.layout = ki.layout; S.modules = ki.modules;
  buildInfo();
  buildKeyboard();
  renderList(); renderPanel();
  scope.on("everest_max.modules", (_t, p) => { S.modules = { numpad: !!(p && p.numpad), dock: !!(p && p.dock) }; buildKeyboard(); });
}

export function unmount() { if (scope) scope.dispose(); scope = null; }
