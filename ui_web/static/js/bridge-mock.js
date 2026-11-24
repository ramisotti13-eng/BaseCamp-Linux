// An in-page fake core for developing the UI without Python: ?mock
// Options: ?mock&lang=de   ?mock&nopad (DisplayPad absent)   window.__mock for poking from the console.
import { STRINGS } from "./strings.js";

const delay = (ms = 15) => new Promise((r) => setTimeout(r, ms));

// A stand-in for the Base Camp page photo (aspect 1.6) with the key screens where the hotspots are measured.
function photoSvg() {
  const W = 1000, H = 625, xs = [0.1536, 0.2721, 0.3907, 0.5093, 0.6279, 0.7464], ys = [0.4473, 0.6176];
  let k = "";
  for (const y of ys) for (const x of xs) k += `<rect x="${x * W}" y="${y * H}" width="${0.1 * W}" height="${0.142 * H}" fill="#222" stroke="#555"/>`;
  return "data:image/svg+xml;utf8," + encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${W} ${H}"><rect width="${W}" height="${H}" fill="#0f1116"/><rect x="100" y="200" width="800" height="330" rx="30" fill="#3a3f4d"/>${k}</svg>`);
}

// The lock screen's preview (612 x 204): "mock:lock:<mode>".
function lockSvg(mode) {
  const body = {
    clock: '<text x="306" y="78" font-size="72" fill="#fff" text-anchor="middle" font-family="sans-serif" font-weight="700">14:05</text><text x="306" y="170" font-size="36" fill="#969caf" text-anchor="middle" font-family="sans-serif" font-weight="700">23.11.2026</text>',
    logo: '<path d="M60 190 L560 20 L470 190Z" fill="#0038e2"/><circle cx="470" cy="80" r="40" fill="#000"/><circle cx="478" cy="74" r="14" fill="#fff"/>',
    picture: '<rect width="612" height="204" fill="#16a34a"/><circle cx="306" cy="102" r="70" fill="#fff" opacity=".85"/>',
  }[mode] || "";
  return "data:image/svg+xml;utf8," + encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 612 204"><rect width="612" height="204" fill="#000"/>${body}</svg>`);
}

function tileSvg(spec) {
  // spec: "mock:RRGGBB:shape"
  if (spec === "mock:photo") return photoSvg();
  if (spec.startsWith("mock:lock:")) return lockSvg(spec.slice(10));
  const [, col, shape] = spec.split(":");
  const c = "#" + col;
  const g = {
    tri: '<path d="M16 88 L64 28 L112 88Z" fill="#fff" opacity=".9"/>',
    sq: '<rect x="34" y="34" width="60" height="60" rx="8" fill="#fff" opacity=".9"/>',
    circ: '<circle cx="64" cy="64" r="34" fill="#fff" opacity=".9"/>',
    bars: '<g fill="#fff" opacity=".9"><rect x="26" y="60" width="16" height="40"/><rect x="50" y="36" width="16" height="64"/><rect x="74" y="50" width="16" height="50"/></g>',
    dot: '<circle cx="64" cy="64" r="14" fill="#fff"/><circle cx="64" cy="64" r="34" fill="none" stroke="#fff" stroke-width="6"/>',
  }[shape] || "";
  return "data:image/svg+xml;utf8," + encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128"><rect width="128" height="128" fill="${c}"/>${g}</svg>`);
}

export function createMock(emit) {
  const params = new URLSearchParams(location.search);
  const lang = params.get("lang") === "de" ? 1 : 0;
  let langCode = lang ? "de" : "en";
  const blank = () => ({ type: "none", action: "" });
  const mk = (n) => Array.from({ length: n }, (_, i) => ({ idx: i, image: null, action: blank() }));
  const pages = [{ id: 0, name: "Main" }, { id: 1, name: "Editor" }, { id: 2, name: "Streaming" }];
  const keys = { 0: mk(12), 1: mk(12), 2: mk(12) };
  const fsOf = {};    // page -> {kind, path}: the page's fullscreen
  const set = (p, i, image, action) => { keys[p][i] = { idx: i, image, action }; };
  set(0, 0, "mock:0038e2:tri", { type: "page", action: "Editor", target: "Editor" });
  set(0, 1, "mock:7c3aed:circ", { type: "obs", action: "Scene: Gameplay", double: { type: "keypress", action: "F12" } });
  set(0, 2, "mock:fd9d02:sq", { type: "shell", action: "notify-send hello" });
  set(0, 3, "mock:16a34a:bars", { type: "url", action: "https://example.org" });
  set(0, 5, null, { type: "keypress", action: "ctrl+shift+a" });
  set(0, 7, "mock:e11d48:dot", { type: "text", action: "Hello world" });
  set(0, 8, "mock:0ea5e9:circ", { type: "macro", action: "Copy all" });
  set(0, 10, "mock:0038e2:bars", { type: "folder", action: "/home/user/Documents" });
  set(0, 4, null, { type: "clock", action: "digital,24h" });
  set(0, 6, null, { type: "pc_info", action: "cpu" });
  set(1, 0, "mock:fd9d02:sq", { type: "keypress", action: "ctrl+s" });
  set(1, 1, "mock:0038e2:tri", { type: "keypress", action: "ctrl+z" });
  set(1, 2, "mock:16a34a:dot", { type: "text", action: "TODO" });
  set(1, 3, "mock:e11d48:circ", { type: "page", action: "Main", target: "Main" });
  for (const i of [0, 1, 2, 3, 4, 5]) set(2, i, ["mock:7c3aed:circ", "mock:e11d48:dot", "mock:0ea5e9:bars", "mock:fd9d02:tri", null, "mock:16a34a:sq"][i], { type: i === 4 ? "keypress" : "obs", action: i === 4 ? "F9" : "Scene " + (i + 1) });

  const S = {
    present: { displaypad: !params.has("nopad"), makalu67: true, everest_max: false, everest60: false, macropad: false },
    current: 0, prev: 0, nextId: 3,
    settings: { rotation: 0, brightness: 70, debounce: 0.3, min_ms: 40 },
    // core/devices/displaypad/power.py: auto-off and the lock screen
    power: { auto_off: { enabled: false, seconds: 10 }, lock: { mode: "none", picture: null, hours: 24 },
      locked: false, sleeping: false, dark: false, screen: false, watching: !params.has("nolockwatch") },
  };
  const strings = () => { const o = {}; for (const [k, v] of Object.entries(STRINGS)) o[k] = v[lang]; return o; };
  const pname = (id) => (pages.find((p) => p.id === id) || {}).name;
  const resolve = (w) => { const p = pages.find((x) => x.name === w); if (p) return p.id; const n = Number(w); return pages.some((x) => x.id === n) ? n : null; };
  const ev = (t, p) => setTimeout(() => emit([[t, p]]), 30);
  const powerState = () => JSON.parse(JSON.stringify(S.power));
  const err = (e) => ({ ok: false, error: e });

  const TYPES = [
    { id: "none", kind: "none" }, { id: "shell" }, { id: "url" }, { id: "folder", kind: "folder" }, { id: "app", kind: "file" },
    { id: "page", kind: "page" },
    { id: "obs", label: "OBS", kind: "select", options: ["Scene: Gameplay", "Scene: Chatting", "Scene 1", "Scene 2", "Scene 3", "Scene 4", "Scene 5", "Scene 6"] },
    { id: "macro", kind: "select", options: ["Copy all", "Paste twice", "Greeting"] },
    { id: "keypress", placeholder_key: "dp_keypress_hint_short" }, { id: "text", placeholder_key: "action_type_text_hint" }, { id: "set_key" },
    { id: "clock", kind: "clock" },
    { id: "pc_info", kind: "select", options: [{ value: "cpu", label: "CPU" }, { value: "ram", label: "RAM" }, { value: "disk", label: "Disk (/)" }, { value: "net", label: "Network (MB/s)" }] },
    { id: "_separator" },
    { id: "plugin_clock", label: "Clock widget", kind: "none" },
  ];

  const commands = {
    "devices.state": () => ({ ok: true, present: { ...S.present }, denied: {}, keyboard_id: "everest_max" }),
    "control.ping": () => ({ ok: true, app: "BaseCamp Linux", version: "4.0.0-dev" }),
    "device_images.status": () => params.has("photo")
      ? { ok: true, imported: true, devices: { displaypad: { page: "mock:photo" } }, sources: { displaypad: { page: "DKD/DKD_Setting.png" } } }
      : { ok: true, imported: false, devices: {}, sources: {} },
    "i18n.strings": () => ({ ok: true, lang: langCode, strings: strings() }),
    "i18n.languages": () => ({ ok: true, languages: { en: "English", de: "Deutsch" }, current: langCode }),
    "i18n.set_language": ({ code }) => { langCode = code; location.search = "?mock&lang=" + code; return { ok: true, code }; },
    "displaypad.status": () => ({ ok: true, present: S.present.displaypad, ready: S.present.displaypad, busy: false, uploading: false, animating: false,
      page: S.current, prev_page: S.prev, settings: { ...S.settings }, power: powerState() }),
    "displaypad.list_pages": () => ({ ok: true, pages: pages.map((p) => ({ ...p })), current: S.current }),
    "displaypad.keys": ({ page }) => {
      const p = page == null ? S.current : resolve(page);
      if (p == null) return err("no such page");
      return { ok: true, page: p, fullscreen: fsOf[p] || null, panel: fsOf[p] ? { w: 800, h: 240, keys: Array.from({ length: 12 }, (_, i) => [Math.round(i % 6 * 139.6), Math.floor(i / 6) * 138, 102, 102]) } : null, keys: JSON.parse(JSON.stringify(keys[p])) };
    },
    "displaypad.set_fullscreen": ({ path, page }) => {
      const p = page == null ? S.current : resolve(page); if (p == null) return err("no such page");
      const gif = /\.gif$/i.test(path || "");
      fsOf[p] = { kind: gif ? "gif" : "picture", path };
      ev("displaypad.fullscreen_changed", { page: p }); ev("displaypad.key_changed", { page: p, idx: null });
      return { ok: true, page: p, kind: fsOf[p].kind, queued: false };
    },
    "displaypad.clear_fullscreen": ({ page }) => {
      const p = page == null ? S.current : resolve(page); if (p == null) return err("no such page");
      const had = !!fsOf[p];
      delete fsOf[p];
      ev("displaypad.fullscreen_changed", { page: p }); ev("displaypad.key_changed", { page: p, idx: null });
      return { ok: true, page: p, cleared: had };
    },
    "displaypad.action_types": () => ({ ok: true, types: TYPES }),
    "displaypad.library": () => {
      const cols = ["0038e2", "fd9d02", "16a34a", "e11d48", "7c3aed", "0ea5e9"], shapes = ["tri", "sq", "circ", "bars", "dot"];
      const entries = [];
      for (let i = 0; i < 9; i++) entries.push({ path: `mock:${cols[i % 6]}:${shapes[i % 5]}`, name: "own" + i, pack: null });
      for (let i = 0; i < 24; i++) entries.push({ path: `mock:${cols[(i + 3) % 6]}:${shapes[(i + 2) % 5]}`, name: "Icon_" + i, pack: "Mountain Icon Pack" });
      return { ok: true, folder: "/mock/displaypad_library", entries, packs: ["Mountain Icon Pack"] };
    },
    "displaypad.switch_page": ({ page }) => {
      const p = resolve(page); if (p == null) return err("no such page");
      const changed = p !== S.current; S.prev = S.current; S.current = p;
      if (changed) ev("displaypad.page_switched", { page: p, prev: S.prev, show_hints: true });
      return { ok: true, page: p, name: pname(p), changed };
    },
    "displaypad.set_action": ({ key, type = "none", action = "", page, target, double }) => {
      const p = page == null ? S.current : resolve(page); if (p == null) return err("no such page");
      const old = keys[p][key];
      const entry = { type, action };
      if (type === "page") {
        let tid = target;
        if (tid === "new" || tid == null) { tid = S.nextId++; pages.push({ id: tid, name: action || "Page " + tid }); keys[tid] = mk(12); ev("displaypad.pages_changed", {}); }
        entry.target = pname(Number(tid));
      }
      const d = double === undefined ? old.action.double : double;
      if (d && d.type && d.type !== "none") entry.double = d;
      keys[p][key] = { idx: key, image: type === old.action.type ? old.image : null, action: entry, text: old.text || null };
      ev("displaypad.key_changed", { page: p, idx: key, origin: "ui" });
      return { ok: true };
    },
    "displaypad.set_image": ({ idx, path, page }) => {
      const p = page == null ? S.current : resolve(page); if (p == null) return err("no such page");
      keys[p][idx].image = path ? (path.startsWith("mock:") ? path : "mock:0038e2:sq") : null;
      ev("displaypad.key_changed", { page: p, idx, origin: "ui" });
      return { ok: true };
    },
    "displaypad.fonts": () => ({ ok: true, fonts: ["DejaVu Sans", "DejaVu Serif", "DejaVu Sans Mono", "Liberation Sans", "Liberation Serif", "Liberation Mono"], default: "DejaVu Sans" }),
    "displaypad.set_key_text": ({ key, page, text }) => {
      const p = page == null ? S.current : resolve(page); if (p == null) return err("no such page");
      if (!(key >= 0 && key < 12)) return err("a key from 0 to 11 is needed");
      let t = null;
      if (text && String(text.text || "").trim()) {
        t = { text: String(text.text).trim().slice(0, 50), pos: ["top", "center", "bottom"].includes(text.pos) ? text.pos : "bottom",
          font: text.font || "DejaVu Sans", size_pt: Math.max(6, Math.min(18, Number(text.size_pt) || 12)),
          bold: !!text.bold, italic: !!text.italic, underline: !!text.underline, color: text.color === "black" ? "black" : "white" };
      }
      keys[p][key].text = t;
      ev("displaypad.key_changed", { page: p, idx: key, origin: "ui" });
      return { ok: true, page: p, key, text: t };
    },
    "displaypad.clear_key": ({ key }) => { keys[S.current][key] = { idx: key, image: null, action: blank() }; ev("displaypad.key_changed", { page: S.current, idx: key }); return { ok: true }; },
    "displaypad.create_page": ({ title }) => { const id = S.nextId++; pages.push({ id, name: title }); keys[id] = mk(12); ev("displaypad.pages_changed", {}); return { ok: true, page: id }; },
    "displaypad.rename_page": ({ page, title }) => { const p = pages.find((x) => x.id === resolve(page)); if (!p) return err("no such page"); p.name = title; ev("displaypad.pages_changed", {}); return { ok: true }; },
    "displaypad.delete_page": ({ page }) => {
      const id = resolve(page); if (id === 0 || id == null) return err("that page cannot be deleted");
      pages.splice(pages.findIndex((x) => x.id === id), 1); delete keys[id];
      if (S.current === id) S.current = 0;
      ev("displaypad.pages_changed", {}); return { ok: true };
    },
    "displaypad.page_references": ({ page }) => {
      const name = pname(resolve(page)); const refs = [];
      for (const [pid, arr] of Object.entries(keys)) arr.forEach((k) => { if (k.action.type === "page" && k.action.target === name) refs.push({ from_page: Number(pid), key: k.idx, kind: "action" }); });
      return { ok: true, references: refs };
    },
    "displaypad.set_brightness": ({ percent }) => { S.settings.brightness = percent; ev("displaypad.settings_changed", { ...S.settings }); return { ok: true, brightness: percent }; },
    "displaypad.set_rotation": ({ degrees }) => { S.settings.rotation = degrees; ev("displaypad.settings_changed", { ...S.settings }); return { ok: true, rotation: degrees }; },
    "displaypad.set_debounce": ({ seconds }) => { S.settings.debounce = seconds; return { ok: true, debounce: seconds }; },
    "displaypad.set_min_frame_ms": ({ ms }) => { S.settings.min_ms = ms; return { ok: true, min_ms: ms }; },
    "displaypad.power": () => ({ ok: true, ...powerState() }),
    "displaypad.set_auto_off": ({ enabled, seconds }) => {
      const ao = S.power.auto_off;
      if (seconds != null && !(Number(seconds) >= 1)) return err("set_auto_off: at least one second");
      if (enabled != null) ao.enabled = !!enabled;
      if (seconds != null) ao.seconds = Math.floor(Number(seconds));
      ev("displaypad.power_changed", powerState());
      return { ok: true, ...powerState() };
    },
    "displaypad.set_lock_screen": ({ mode, picture, hours }) => {
      const lk = S.power.lock;
      if (mode != null && !["none", "clock", "picture", "logo", "off"].includes(mode)) return err("set_lock_screen: mode is one of none, clock, picture, logo, off");
      if (mode === "picture" && !picture && !lk.picture) return err("set_lock_screen: a picture is needed");
      if (hours != null && ![12, 24].includes(Number(hours))) return err("set_lock_screen: hours is 12 or 24");
      if (mode != null) lk.mode = mode;
      if (picture) lk.picture = picture;
      if (hours != null) lk.hours = Number(hours);
      ev("displaypad.power_changed", powerState());
      return { ok: true, ...powerState() };
    },
    "displaypad.preview_lock_screen": ({ mode }) => {
      const m = mode || S.power.lock.mode;
      return { ok: true, path: ["clock", "picture", "logo"].includes(m) ? "mock:lock:" + m : null };
    },
  };

  window.__mock = {
    state: S, pages, keys,
    // Lock or unlock the session (or put it to sleep): what the pad would show.
    setLocked(v) {
      S.power.locked = !!v;
      const m = S.power.lock.mode;
      S.power.screen = S.power.locked && ["clock", "picture", "logo"].includes(m);
      S.power.dark = S.power.locked && m === "off";
      ev("displaypad.power_changed", powerState());
    },
    setPresent(id, v) { S.present[id] = v; if (id === "displaypad") ev(v ? "displaypad.device_connected" : "displaypad.device_disconnected", {}); ev("devices.changed", { present: { ...S.present }, denied: {}, keyboard_id: "everest_max" }); },
    emit: (t, p) => emit([[t, p]]),
  };

  return {
    name: "mock",
    async call(cmd, p) {
      await delay();
      const fn = commands[cmd];
      return fn ? fn(p || {}) : err("unknown command: " + cmd);
    },
    async image(path) { await delay(5); return path && path.startsWith("mock:") ? tileSvg(path) : null; },
    async pickFile(opts) {
      const k = opts && opts.kind;
      return k === "image" ? "/mock/picture.png" : k === "folder" ? "/mock/folder" : k === "save" ? "/mock/" + ((opts && opts.name) || "export.json") : "/mock/file";
    },
    async open(url) { console.info("[mock] open", url); },
    win: { minimize() { console.info("[mock] minimize"); }, toggleMaximize() {}, close() {} },
  };
}
