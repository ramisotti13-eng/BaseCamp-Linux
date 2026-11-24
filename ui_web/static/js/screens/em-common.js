// Shared by the Everest Max screens: the frame with its icon rail, the job tracker and status line,
// and the data of the flat keyboard picture (keyboard-flat.js) they all draw.
import bc from "../bridge.js";
import { t } from "../i18n.js";
import * as devices from "../devices.js";
import { h, icon, clear, toast } from "../ui.js";
import { currentLang } from "../i18n.js";
import { railItem } from "../profiles-page.js";

export const DEV = "everest_max";

// ── icons (the symbols of this device's rail and effect list; the shell's live in index.html) ─────
const SYMBOLS = `<svg width="0" height="0" style="position:absolute" aria-hidden="true"><defs>
<symbol id="i-bulb" viewBox="0 0 28 28"><path fill="currentColor" d="M14 3a7.5 7.5 0 00-4.3 13.6c.6.5.8 1 .8 1.7V19h7v-.7c0-.7.2-1.2.8-1.7A7.5 7.5 0 0014 3zM10.5 21h7v2h-7zM12 24h4v1.5h-4z"/></symbol>
<symbol id="i-bulb-off" viewBox="0 0 24 24"><path fill="none" stroke="currentColor" stroke-width="2" d="M12 3a6 6 0 00-3.5 10.9c.5.4.8.9.8 1.5V16h5.4v-.6c0-.6.3-1.1.8-1.5A6 6 0 0012 3zM9.5 19h5M10.5 21.5h3"/></symbol>
<symbol id="i-monitor" viewBox="0 0 28 28"><rect x="3" y="5" width="22" height="14" rx="2" fill="currentColor"/><rect x="6" y="8" width="16" height="8" fill="#0f1116"/><path fill="currentColor" d="M10 21h8v2h-8z"/></symbol>
<symbol id="i-sun" viewBox="0 0 24 24"><circle cx="12" cy="12" r="4" fill="currentColor"/><path stroke="currentColor" stroke-width="2" stroke-linecap="round" d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M18.4 5.6l-2.1 2.1M7.7 16.3l-2.1 2.1"/></symbol>
<symbol id="i-wave" viewBox="0 0 24 24"><path fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" d="M3 8q3-3 6 0t6 0 6 0M3 13q3-3 6 0t6 0 6 0M3 18q3-3 6 0t6 0 6 0"/></symbol>
<symbol id="i-tornado" viewBox="0 0 24 24"><path fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" d="M4 6h16M6 10h12M8 14h8M10 18h4"/></symbol>
<symbol id="i-breath" viewBox="0 0 24 24"><g fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/></g><circle cx="12" cy="12" r="1.6" fill="currentColor"/></symbol>
<symbol id="i-bolt" viewBox="0 0 24 24"><path fill="currentColor" d="M13 2L5 14h6l-1 8 8-12h-6z"/></symbol>
<symbol id="i-matrix" viewBox="0 0 24 24"><path stroke="currentColor" stroke-width="2.4" stroke-linecap="round" d="M5 4v6M5 14v2M10 7v8M10 19v1M15 4v3M15 11v7M20 6v5M20 15v3"/></symbol>
<symbol id="i-brush" viewBox="0 0 24 24"><path fill="currentColor" d="M20.7 5.6l-2.3-2.3a1 1 0 00-1.4 0L9 11.3l3.7 3.7 8-8a1 1 0 000-1.4zM7.5 13C5.6 13 4 14.6 4 16.5 4 18 3 19 2 19.5c2.5.8 6.5.6 8-1.2l.5-1-3-4.3z"/></symbol>
<symbol id="i-paw" viewBox="0 0 24 24"><g fill="currentColor"><ellipse cx="6.5" cy="10" rx="2" ry="2.6"/><ellipse cx="10.5" cy="6" rx="2" ry="2.6"/><ellipse cx="15.5" cy="6" rx="2" ry="2.6"/><ellipse cx="19" cy="10.5" rx="1.9" ry="2.5"/><path d="M12.5 11c-3 0-6 3-6 6 0 2 1.7 2.8 3.3 2.4 1.2-.4 1.8-.4 2.7-.4s1.5 0 2.7.4c1.6.4 3.3-.4 3.3-2.4 0-3-3-6-6-6z"/></g></symbol>
<symbol id="i-dial" viewBox="0 0 28 28"><g fill="none" stroke="currentColor" stroke-width="2"><circle cx="14" cy="14" r="10"/><path d="M14 4v6" stroke-linecap="round"/></g></symbol>
</defs></svg>`;

function ensureSymbols() {
  if (document.getElementById("em-symbols")) return;
  const tpl = document.createElement("template");
  tpl.innerHTML = SYMBOLS;      // our own static symbols, no user data
  const sprite = tpl.content.firstChild;
  sprite.id = "em-symbols";
  // Only the symbols the page does not have yet: another screen (or index.html) may already
  // define one of them, and two elements with the same id would make <use> pick either.
  for (const sym of [...sprite.querySelectorAll("symbol")]) {
    if (document.getElementById(sym.id)) sym.remove();
  }
  document.body.append(sprite);
}

// ── frame ──────────────────────────────────────────────────────────────────

const RAIL = [
  ["rgb", "bulb", "em_rail_rgb"], ["keys", "keys", "em_rail_keys"],
  ["macro", "macro-m", "em_rail_macro"], ["display", "dial", "em_rail_display"], ["settings", "sliders", "em_rail_settings"],
];

/** Builds .devpage with the rail; returns {root, col, main}. `withCol` adds the middle column. */
export function frame(el, active, withCol) {
  ensureSymbols();
  clear(el);
  const rail = h("nav", { class: "rail", "aria-label": "Everest Max" },
    railItem(DEV, active),
    RAIL.map(([id, ic, key]) => h("a", { href: "#/everest_max/" + id, class: active === id ? "on" : "", "aria-current": active === id ? "page" : null },
      icon(ic), t(key))));
  const col = withCol ? h("section", { class: "col" }) : null;
  const main = h("section", { class: "main" });
  const root = h("div", { class: "devpage" + (withCol ? "" : " nocol") }, rail, col, main);
  el.append(root);
  // The Display / dial entry only exists with the media dock attached (what the keyboard reports;
  // while that is unknown, e.g. Monitor Mode is off, it stays).
  const showDial = (m) => {
    const a = rail.querySelector('a[href="#/everest_max/display"]');
    if (a) a.hidden = !!(m && m.dock === false);
  };
  bc.call("everest_max.modules").then((m) => { if (m && m.ok) showDial(m); });
  const off = bc.on("everest_max.modules", (_t, p) => {
    if (!root.isConnected) { off(); return; }
    showDial({ dock: p && p.dock });
  });
  return { root, col, main };
}

export async function cmd(name, params) {
  const r = await bc.call("everest_max." + name, params);
  if (!r.ok) toast(t("ui_error", { err: r.error }), "err");
  return r;
}

// ── jobs and the status line ──────────────────────────────────────────────────

const HIDDEN_KINDS = new Set(["per_key_preview"]);

export function jobLabel(job) {
  const k = "em_job_" + (job.kind === "per_key" && job.effects ? "per_key_fx" : job.kind === "per_key" && job.persist ? "persist" : job.kind);
  const s = t(k, { d: job.button != null ? job.button + 1 : "", effect: job.effect || "", mode: job.mode || "" });
  return s === k ? t("em_job_other", { kind: job.kind }) : s;
}

/** A thin progress bar for a job; indeterminate (moving) while there is no percentage yet. */
export function progressBar(job) {
  const known = typeof job.pct === "number";
  return h("div", { class: "pbar" + (known ? "" : " busy"), role: "progressbar", "aria-valuemin": 0, "aria-valuemax": 100,
    "aria-valuenow": known ? job.pct : null, "aria-label": jobLabel(job) },
    h("i", { style: known ? "width:" + Math.max(2, Math.min(100, job.pct)) + "%" : null }));
}

/** Follows the keyboard's jobs and Monitor Mode from the service's events for the life of a screen.
 *  state: {present, jobs: Map(id -> {id, kind, pct, stage, t0, button, ...}), monitor}. */
export function tracker(scope) {
  const st = { present: devices.isPresent(DEV), jobs: new Map(), monitor: { active: false, wanted: false, paused: false, error: "", code: null }, main_mode: null, style: null, format: null };
  const subs = new Set(), doneSubs = new Set();
  const changed = () => subs.forEach((fn) => fn(st));
  const add = (p) => {
    const j = { ...p, pct: typeof p.pct === "number" ? p.pct : null, t0: Date.now() };
    st.jobs.set(p.id, j);
    return j;
  };
  scope.add(devices.onChange(() => { st.present = devices.isPresent(DEV); changed(); }));
  scope.on("everest_max.*", (topic, p) => {
    p = p || {};
    switch (topic.slice(DEV.length + 1)) {
      case "job_started": if (!HIDDEN_KINDS.has(p.kind)) add(p); break;
      case "job_progress": {
        if (HIDDEN_KINDS.has(p.kind)) return;
        const j = st.jobs.get(p.id) || add(p);
        if (typeof p.pct === "number") j.pct = p.pct;
        if (p.stage) j.stage = p.stage;
        if (p.attempt != null) j.attempt = p.attempt;
        break;
      }
      case "job_done": {
        const j = st.jobs.get(p.id);
        st.jobs.delete(p.id);
        doneSubs.forEach((fn) => fn(p, j));
        break;
      }
      case "monitor": st.monitor = { ...st.monitor, ...p }; break;
      case "main_mode": st.main_mode = p.mode; break;
      case "clock": st.style = p.style; st.format = p.format; break;
      default: return;
    }
    changed();
  });
  bc.call("everest_max.status").then((r) => {
    if (!scope.alive || !r.ok) return;
    st.monitor = { ...st.monitor, ...r.monitor };
    st.main_mode = r.main_mode; st.style = r.style; st.format = r.clock_format;
    for (const j of r.jobs || []) if (!st.jobs.has(j.id) && !HIDDEN_KINDS.has(j.kind)) add(j);
    changed();
  });
  return { st, onChange(fn) { subs.add(fn); }, onDone(fn) { doneSubs.add(fn); }, changed };
}

/** The line above the main area: "not connected", and a chip with progress for every running job.
 *  A picture written to a D-key says it takes 45-60 s. Returns {el, tracker}. */
export function statusBar(scope, tr) {
  tr = tr || tracker(scope);
  const el = h("div", { class: "status", role: "status" });
  let timer = null;
  const render = () => {
    clear(el);
    const st = tr.st;
    if (st.present === false) el.append(h("span", { class: "chip warn" }, t("em_no_device") + " · " + t("no_device_hint")));
    for (const j of st.jobs.values()) {
      const secs = Math.round((Date.now() - j.t0) / 1000);
      const bits = [jobLabel(j)];
      if (j.stage === "retry") bits.push(t("em_job_retry", { n: j.attempt || 1 }));
      if (j.kind === "dbutton_upload" || j.kind === "main_upload" || j.kind === "dpush") bits.push(secs + " s");
      if (typeof j.pct === "number") bits.push(j.pct + " %");
      el.append(h("span", { class: "chip job" }, bits.join(" · "), progressBar(j)));
      if (j.kind === "dbutton_upload") el.append(h("span", { class: "chip warn" }, t("em_flash_note")));
    }
    clearTimeout(timer);
    if (st.jobs.size) timer = scope.later(render, 1000);
  };
  tr.onChange(render);
  render();
  return { el, tracker: tr };
}

/** The flat keyboard's data: the layout (everest_max.layout) and which modules are attached
 *  (everest_max.modules; unknown means both are drawn). */
export async function loadKeyboardInfo() {
  const [lr, mr] = await Promise.all([bc.call("everest_max.layout", { lang: currentLang() }), bc.call("everest_max.modules")]);
  const modules = { numpad: true, dock: true, known: false };
  if (mr && mr.ok) { modules.numpad = !!mr.numpad; modules.dock = !!mr.dock; modules.known = !!mr.known; modules.source = mr.source; }
  return { layout: lr && lr.ok ? lr : null, modules };
}

