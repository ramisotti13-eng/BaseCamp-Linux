// Macros: the list (new, duplicate, delete, import, export) and an editor for the name, the
// repeat mode and the steps, with key recording from the page's own key events.
// Mouse-path recording (a full-screen overlay in the classic interface) is not part of this screen.
import bc from "../bridge.js";
import { t } from "../i18n.js";
import { h, icon, clear, toast, makeScope, confirmDialog } from "../ui.js";
import { frame, panel, colHead, iconBtn, errText } from "./set-common.js";

// What a step can be added as. A recorded mouse path (mouse_path) is shown and kept, not made here.
const FALLBACK_TYPES = ["key_tap", "key_down", "key_up", "text", "delay", "mouse_click", "mouse_move", "mouse_scroll", "shell", "url", "folder"];
const DEFAULT_VALUE = {
  key_tap: "a", key_down: "ctrl", key_up: "ctrl", text: "", delay: "100", mouse_click: "left",
  mouse_move: "0, 0", mouse_scroll: "down 3", shell: "", url: "https://", folder: "~",
};
const KEY_TYPES = ["key_tap", "key_down", "key_up"];
const BUTTONS = ["left", "middle", "right", "back", "forward"];
const OWNER = "ui";

let scope = null;
let stopRecording = null;

const stepLabel = (type) => t("macro_step_" + type);

/** A step as the core stores it. */
function norm(step) {
  const d = parseInt(step && step.delay, 10);
  return { type: (step && step.type) || "", value: step && step.value != null ? String(step.value) : "", delay: Number.isFinite(d) ? d : 0 };
}

function draftOf(m) {
  const n = parseInt(m.repeat_count, 10);
  return {
    name: m.name || "", repeat_mode: m.repeat_mode || "once", repeat_count: Number.isFinite(n) && n > 0 ? n : 1,
    actions: (m.actions || []).map(norm),
  };
}

/** Shows the library in the frame `makeFrame()` builds: {root, col, main} with the middle column, either a
 *  device's frame with its Macro entry or the gear's own. */
export async function mountIn(el, makeFrame) {
  scope = makeScope();
  const fr = makeFrame();
  const S = {
    macros: {}, sel: null, base: null, draft: null, types: FALLBACK_TYPES, keys: [],
    tool: undefined, places: [], running: new Map(),
  };
  const statusHost = h("div", { class: "status", role: "status" });
  // The editor fills the window's height; only the list of steps scrolls, within itself (app.css .maced).
  const editorHost = h("div", { class: "maced" });
  fr.main.append(statusHost, editorHost);

  const dirty = () => !!S.draft && JSON.stringify(S.draft) !== JSON.stringify(S.base);
  const sorted = () => Object.entries(S.macros).sort((a, b) => (a[1].name || "").toLowerCase().localeCompare((b[1].name || "").toLowerCase()));
  const isRunning = (id) => (S.running.get(id) || 0) > 0;

  // ── data ────────────────────────────────────────────────────────────────
  async function loadAll() {
    const r = await bc.call("macros.list");
    if (!scope.alive) return;
    if (!r.ok) { toast(errText(r), "err"); return; }
    S.macros = r.macros || {};
    const gone = S.sel && !S.macros[S.sel];
    if (gone) { S.sel = null; S.draft = S.base = null; }
    renderList();
    if (gone) renderEditor();
  }

  async function select(id) {
    if (stopRecording) stopRecording();
    S.sel = id;
    const m = id ? S.macros[id] : null;
    S.base = m ? draftOf(m) : null;
    S.draft = S.base ? JSON.parse(JSON.stringify(S.base)) : null;
    S.places = [];
    renderList();
    renderEditor();
    if (id) {
      const r = await bc.call("macros.find_assignments", { macro_id: id });
      if (scope.alive && S.sel === id) { S.places = r.ok ? r.places || [] : []; renderPlaces(); }
    }
  }

  async function guardDirty() {
    if (!dirty()) return true;
    return confirmDialog({ title: t("dp_discard_title"), message: t("set_macro_discard_msg"), okLabel: t("dp_discard_btn"), danger: true });
  }

  async function pick(id) {
    if (id === S.sel) return;
    if (!(await guardDirty())) return;
    await select(id);
  }

  async function newMacro() {
    if (!(await guardDirty())) return;
    const r = await bc.call("macros.create", { fields: {} });
    if (!r.ok) return toast(errText(r), "err");
    await loadAll();
    await select(r.id);
  }

  async function duplicate(id) {
    const r = await bc.call("macros.duplicate", { macro_id: id });
    if (!r.ok) return toast(errText(r), "err");
    await loadAll();
    if (await guardDirty()) await select(r.id);
  }

  async function remove(id) {
    const name = (S.macros[id] || {}).name || id;
    const ok = await confirmDialog({ title: t("macro_delete"), message: t("set_macro_delete_confirm", { name }), okLabel: t("macro_delete"), danger: true });
    if (!ok) return;
    const r = await bc.call("macros.delete", { macro_id: id });
    if (!r.ok) return toast(errText(r), "err");
    if (S.sel === id) { S.sel = null; S.base = S.draft = null; }
    await loadAll();
    renderEditor();
  }

  async function importFile() {
    const path = await bc.pickFile({ title: t("macro_import"), kind: "any" });
    if (!path) return;
    const r = await bc.call("macros.import_file", { path });
    if (!r.ok) return toast(t("set_macro_import_err", { err: r.error }), "err");
    toast(t("set_macro_imported"), "ok");
    await loadAll();
    if (await guardDirty()) await select(r.id);
  }

  async function exportFile(id) {
    const name = ((S.macros[id] || {}).name || "macro").replace(/[\\/:*?"<>|]+/g, "_") + ".json";
    const path = await bc.pickFile({ title: t("macro_export"), kind: "save", name });
    if (!path) return;
    const r = await bc.call("macros.export_file", { macro_id: id, path });
    if (!r.ok) return toast(errText(r), "err");
    toast(t("set_macro_exported", { path }), "ok");
  }

  async function save() {
    if (!S.sel || !S.draft) return true;
    const d = S.draft;
    const fields = { name: d.name, repeat_mode: d.repeat_mode, repeat_count: d.repeat_count, actions: d.actions };
    const r = await bc.call("macros.update", { macro_id: S.sel, fields });
    if (!r.ok) { toast(errText(r), "err"); return false; }
    S.macros[S.sel] = r.macro;
    S.base = draftOf(r.macro);
    S.draft = JSON.parse(JSON.stringify(S.base));
    toast(t("settings_saved"), "ok");
    renderList();
    renderEditor();
    return true;
  }

  async function run() {
    if (dirty() && !(await save())) return;
    const r = await bc.call("macros.run", { macro_id: S.sel, owner: OWNER });
    if (!r.ok) return toast(errText(r), "err");
    if (r.result === "running") toast(t("set_macro_already_running"));
  }

  async function stop() {
    const r = await bc.call("macros.stop", { macro_id: S.sel });
    if (!r.ok) toast(errText(r), "err");
  }

  // ── the list ──────────────────────────────────────────────────────────────
  function modeText(m) {
    if (m.repeat_mode === "toggle") return t("macro_repeat_toggle");
    if (m.repeat_mode === "repeat") return t("macro_repeat_n") + " · " + (m.repeat_count || 1);
    return t("macro_repeat_once");
  }

  function renderList() {
    const col = fr.col;
    const keep = col.contains(document.activeElement) ? document.activeElement.getAttribute("data-f") : null;
    clear(col);
    col.append(
      colHead(t("macro_title")),
      h("button", { class: "newbtn", type: "button", onclick: newMacro }, icon("plus"), t("macro_new")),
      h("button", { class: "btn", type: "button", style: "margin:0 14px 6px", onclick: importFile }, icon("folder"), t("macro_import")));
    const rows = h("div", { class: "rows" });
    const list = sorted();
    if (!list.length) rows.append(h("div", { class: "empty" }, t("macro_no_macros")));
    for (const [id, m] of list) {
      const n = (m.actions || []).length;
      rows.append(h("div", { class: "row" + (id === S.sel ? " on" : "") },
        h("button", { class: "pick", type: "button", "data-f": "pick" + id, "aria-pressed": id === S.sel ? "true" : "false", onclick: () => pick(id) },
          h("div", { class: "tile" }, icon("macro")),
          h("div", { class: "txt" },
            h("div", { class: "nm h" }, m.name || id),
            h("div", { class: "sub" }, isRunning(id) ? h("span", { class: "badge", style: "margin:0 6px 0 0" }, t("set_macro_running")) : null,
              t("set_macro_summary", { n, mode: modeText(m) })))),
        h("div", { class: "acts" },
          iconBtn("copy", t("macro_duplicate") + ": " + m.name, () => duplicate(id)),
          iconBtn("trash", t("macro_delete") + ": " + m.name, () => remove(id)))));
    }
    col.append(rows);
    if (keep) { const f = col.querySelector(`[data-f="${keep}"]`); if (f) f.focus(); }
  }

  // ── the editor ────────────────────────────────────────────────────────────
  let saveBtn = null;
  let placesEl = null;
  const changed = () => { if (saveBtn) saveBtn.disabled = !dirty(); };

  function renderPlaces() {
    if (!placesEl || !placesEl.isConnected) return;
    clear(placesEl);
    placesEl.append(S.places.length
      ? t("macro_assigned_to", { places: S.places.map((p) => p.label).join(", ") })
      : t("macro_unassigned"));
  }

  function renderStatus() {
    clear(statusHost);
    if (S.tool === null) statusHost.append(h("span", { class: "chip warn" }, t("macro_no_tool")));
  }

  function renderEditor() {
    if (stopRecording) stopRecording();
    clear(editorHost);
    saveBtn = null;
    if (!S.draft) {
      editorHost.append(panel(t("macro_title"), h("div", { class: "empty", style: "padding:20px 0" }, Object.keys(S.macros).length ? t("set_macro_pick") : t("macro_no_macros"))));
      return;
    }
    const d = S.draft;
    const name = h("input", { class: "field", id: "mac-name", type: "text", value: d.name, autocomplete: "off" });
    name.addEventListener("input", () => { d.name = name.value; changed(); });
    const mode = h("select", { class: "field", id: "mac-mode" }, [
      ["once", t("macro_repeat_once")], ["repeat", t("macro_repeat_n")], ["toggle", t("macro_repeat_toggle")],
    ].map(([v, l]) => h("option", { value: v }, l)));
    mode.value = d.repeat_mode;
    const count = h("input", { class: "field", id: "mac-count", type: "number", min: 1, step: 1, value: d.repeat_count });
    const countBox = h("div", null, h("label", { class: "lab", for: "mac-count" }, t("macro_count_label").replace(/:$/, "")), count);
    const showCount = () => { countBox.style.display = d.repeat_mode === "repeat" ? "" : "none"; };
    mode.addEventListener("change", () => { d.repeat_mode = mode.value; showCount(); changed(); });
    count.addEventListener("input", () => { d.repeat_count = Math.max(1, parseInt(count.value, 10) || 1); changed(); });
    showCount();

    placesEl = h("div", { class: "help", style: "margin-top:12px" });
    const running = isRunning(S.sel);
    saveBtn = h("button", { class: "btn primary", type: "button", disabled: !dirty(), onclick: save }, t("dp_apply"));
    const actions = h("div", { class: "actions" },
      saveBtn,
      h("button", { class: "btn", type: "button", onclick: running && d.repeat_mode === "toggle" ? stop : run },
        icon("play"), running && d.repeat_mode === "toggle" ? t("macro_rec_stop") : t("set_macro_run")),
      h("button", { class: "btn", type: "button", onclick: () => exportFile(S.sel) }, t("macro_export")),
      h("button", { class: "btn danger", type: "button", style: "margin-left:auto", onclick: () => remove(S.sel) }, icon("trash"), t("macro_delete")));

    const stepsHost = h("ol", { class: "steps", "aria-label": t("macro_actions_label") });
    const addType = h("select", { class: "field", id: "mac-add", style: "width:220px" },
      S.types.map((ty) => h("option", { value: ty }, stepLabel(ty))));
    addType.value = "key_tap";
    const addBtn = h("button", { class: "btn", type: "button", onclick: () => {
      d.actions.push({ type: addType.value, value: DEFAULT_VALUE[addType.value] || "", delay: 0 });
      drawSteps(); changed();
      const last = stepsHost.lastElementChild;
      if (last) { const f = last.querySelector("input, select"); if (f) f.focus(); }
    } }, icon("plus"), t("macro_add_action"));

    function drawSteps() {
      if (stopRecording) stopRecording();
      clear(stepsHost);
      if (!d.actions.length) stepsHost.append(h("li", { class: "empty", style: "padding:14px 0" }, t("set_macro_no_steps")));
      d.actions.forEach((step, i) => stepsHost.append(stepRow(step, i)));
    }

    function stepRow(step, i) {
      const move = (delta) => {
        const j = i + delta;
        if (j < 0 || j >= d.actions.length) return;
        [d.actions[i], d.actions[j]] = [d.actions[j], d.actions[i]];
        drawSteps(); changed();
        const li = stepsHost.children[j];
        const btn = li && li.querySelector(delta < 0 ? '[data-act="up"]' : '[data-act="down"]');
        if (btn && !btn.disabled) btn.focus();
      };
      const kind = h("select", { class: "field plain", "aria-label": t("macro_actions_label") + " " + (i + 1) },
        (S.types.includes(step.type) || !step.type ? S.types : [step.type, ...S.types]).map((ty) => h("option", { value: ty }, stepLabel(ty))));
      kind.value = step.type;
      kind.addEventListener("change", () => {
        step.type = kind.value;
        step.value = DEFAULT_VALUE[step.type] != null ? DEFAULT_VALUE[step.type] : "";
        drawSteps(); changed();
      });
      const wait = h("input", { class: "field plain", type: "number", min: 0, step: 10, value: step.delay, "aria-label": t("set_macro_wait_after") + " " + (i + 1), title: t("set_macro_wait_after") });
      wait.addEventListener("input", () => { step.delay = Math.max(0, parseInt(wait.value, 10) || 0); changed(); });
      if (step.type === "delay") { wait.disabled = true; wait.title = t("set_macro_delay_own"); }
      const acts = h("div", { class: "stepacts" },
        h("button", { class: "iconbtn", type: "button", "data-act": "up", title: t("set_macro_up"), "aria-label": t("set_macro_up") + " " + (i + 1), disabled: i === 0, onclick: () => move(-1) }, icon("up")),
        h("button", { class: "iconbtn", type: "button", "data-act": "down", title: t("set_macro_down"), "aria-label": t("set_macro_down") + " " + (i + 1), disabled: i === d.actions.length - 1, onclick: () => move(1) }, icon("down")),
        h("button", { class: "iconbtn", type: "button", title: t("set_macro_remove"), "aria-label": t("set_macro_remove") + " " + (i + 1), onclick: () => { d.actions.splice(i, 1); drawSteps(); changed(); } }, icon("trash")));
      return h("li", { class: "step" }, h("span", { class: "n" }, String(i + 1)), kind, valueEditor(step, i), wait, acts);
    }

    editorHost.append(
      panel(t("macro_title") + " · " + (d.name || S.sel),
        h("div", { class: "grid2" },
          h("div", null, h("label", { class: "lab", for: "mac-name" }, t("macro_name_label").replace(/:$/, "")), name),   // beside the repeat: room for the steps
          h("div", null, h("label", { class: "lab", for: "mac-mode" }, t("macro_repeat_label").replace(/:$/, "")), mode),
          countBox),
        placesEl, actions),
      h("div", { style: "height:18px;flex:none" }),
      panel(t("macro_actions_label"),
        h("div", { class: "help clamp", style: "margin:14px 0 4px", title: t("set_macro_steps_help") }, t("set_macro_steps_help")),
        stepsHost,
        h("div", { class: "inline-row", style: "margin-top:16px;align-items:center" }, addType, addBtn)));
    drawSteps();
    renderPlaces();
  }

  /** The editor of a step's value, by the step's type. It writes into the step. */
  function valueEditor(step) {
    const set = (v) => { step.value = v; changed(); };
    const lab = t("macro_actions_label");
    if (KEY_TYPES.includes(step.type)) {
      const input = h("input", { class: "field plain", type: "text", list: "mac-keys", value: step.value, autocomplete: "off", "aria-label": lab + ": " + t("action_type_keypress"), placeholder: t("dp_keypress_hint_short") });
      input.addEventListener("input", () => set(input.value.trim()));
      const rec = h("button", { class: "btn", type: "button", title: t("set_macro_rec_key_title") }, t("macro_rec_btn"));
      rec.addEventListener("click", () => recordKey(rec, input, step, set));
      return h("div", { class: "inline-row" }, input, rec);
    }
    if (step.type === "delay") {
      const input = h("input", { class: "field plain", type: "number", min: 0, step: 10, value: step.value, "aria-label": t("macro_ms_label").replace(/:$/, "") });
      input.addEventListener("input", () => set(String(Math.max(0, parseInt(input.value, 10) || 0))));
      return h("div", { class: "inline-row" }, input, h("span", { class: "unit" }, "ms"));
    }
    if (step.type === "mouse_click") {
      const sel = h("select", { class: "field plain", "aria-label": stepLabel("mouse_click") },
        (BUTTONS.includes(step.value) ? BUTTONS : [step.value, ...BUTTONS]).map((b) => h("option", { value: b }, BUTTONS.includes(b) ? t("set_macro_btn_" + b) : b)));
      sel.value = step.value;
      sel.addEventListener("change", () => set(sel.value));
      return sel;
    }
    if (step.type === "mouse_move") {
      const [x, y] = step.value.replace(",", " ").split(/\s+/).filter(Boolean);
      const xi = h("input", { class: "field plain", type: "number", step: 1, value: x != null ? x : "0", "aria-label": "X" });
      const yi = h("input", { class: "field plain", type: "number", step: 1, value: y != null ? y : "0", "aria-label": "Y" });
      const upd = () => set((xi.value || "0") + ", " + (yi.value || "0"));
      xi.addEventListener("input", upd); yi.addEventListener("input", upd);
      return h("div", { class: "inline-row" }, h("span", { class: "unit" }, "X"), xi, h("span", { class: "unit" }, "Y"), yi);
    }
    if (step.type === "mouse_scroll") {
      const parts = step.value.toLowerCase().trim().split(/\s+/);
      const dir = h("select", { class: "field plain", "aria-label": stepLabel("mouse_scroll") },
        ["up", "down"].map((d) => h("option", { value: d }, t("set_macro_scroll_" + d))));
      dir.value = parts[0] === "down" ? "down" : "up";
      const cnt = h("input", { class: "field plain", type: "number", min: 1, step: 1, value: parts[1] || "3", "aria-label": t("macro_count_label").replace(/:$/, "") });
      const upd = () => set(dir.value + " " + Math.max(1, parseInt(cnt.value, 10) || 1));
      dir.addEventListener("change", upd); cnt.addEventListener("input", upd);
      return h("div", { class: "inline-row" }, dir, cnt);
    }
    if (step.type === "mouse_path") {
      return h("div", { class: "help", style: "margin:0" }, t("set_macro_path_note", { file: step.value }));
    }
    const ph = step.type === "text" ? t("action_type_text_hint") : "";
    const input = h("input", { class: "field plain", type: "text", value: step.value, autocomplete: "off", "aria-label": stepLabel(step.type), placeholder: ph });
    input.addEventListener("input", () => set(input.value));
    return input;
  }

  /** Record the next key pressed on the page into `input` / `step`, named by the core. */
  function recordKey(btn, input, step, set) {
    const wasMe = !!btn.dataset.rec;
    if (stopRecording) stopRecording();
    if (wasMe) return;                  // a second click on the same button cancels
    btn.dataset.rec = "1";
    btn.classList.add("rec");
    btn.textContent = t("set_macro_press_key");
    let done = false;
    const end = () => {
      done = true;
      document.removeEventListener("keydown", onKey, true);
      btn.removeEventListener("blur", end);
      delete btn.dataset.rec;
      btn.classList.remove("rec");
      btn.textContent = t("macro_rec_btn");
      if (stopRecording === end) stopRecording = null;
    };
    const onKey = async (e) => {
      e.preventDefault(); e.stopPropagation();
      if (e.repeat || done) return;
      const code = e.code, key = e.key;
      end();
      const r = await bc.call("macros.key_from_dom", { code, key });
      if (!scope.alive) return;
      if (r.ok && r.key) { input.value = r.key; set(r.key); }
      else toast(t("set_macro_key_unknown"), "err");
      btn.focus();
    };
    document.addEventListener("keydown", onKey, true);
    btn.addEventListener("blur", end);
    stopRecording = end;
  }

  // ── start ───────────────────────────────────────────────────────────────
  const keyList = h("datalist", { id: "mac-keys" });
  fr.root.append(keyList);
  scope.on("macros.changed", (_t, p) => {
    p = p || {};
    if (p.change === "recordings") return;
    const keepDraft = dirty() && p.id === S.sel;
    loadAll().then(() => {
      if (!scope.alive || p.id !== S.sel || keepDraft) return;
      if (S.sel && S.macros[S.sel]) { S.base = draftOf(S.macros[S.sel]); S.draft = JSON.parse(JSON.stringify(S.base)); renderEditor(); }
      else if (!S.sel) renderEditor();
    });
  });
  scope.on("macros.started", (_t, p) => { const id = (p || {}).id; S.running.set(id, (S.running.get(id) || 0) + 1); renderList(); if (id === S.sel) renderEditor(); });
  scope.on("macros.stopped", (_t, p) => {
    const id = (p || {}).id;
    S.running.set(id, Math.max(0, (S.running.get(id) || 0) - 1));
    renderList(); if (id === S.sel && !dirty()) renderEditor();
  });

  renderList();
  renderEditor();
  const [types, keys, tool, running] = await Promise.all([
    bc.call("macros.step_types"), bc.call("macros.key_choices"), bc.call("macros.tools"), bc.call("macros.running")]);
  if (!scope.alive) return;
  if (types.ok && Array.isArray(types.types)) {
    const known = types.types.filter((x) => x !== "mouse_path");
    if (known.length) S.types = known;
  }
  if (keys.ok && Array.isArray(keys.keys)) S.keys = keys.keys;
  for (const k of S.keys) keyList.append(h("option", { value: k }));
  S.tool = tool.ok ? tool.tool || null : undefined;
  if (running.ok) for (const r of running.running || []) S.running.set(r.id, (S.running.get(r.id) || 0) + 1);
  renderStatus();
  await loadAll();
  if (!scope.alive) return;
  const first = sorted()[0];
  if (first && !S.sel) await select(first[0]);
}

export const mount = (el) => mountIn(el, () => frame(el, "macros", true));

export function unmount() {
  if (stopRecording) stopRecording();
  if (scope) scope.dispose();
  scope = null;
}
