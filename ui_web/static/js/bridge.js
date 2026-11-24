// window.bc: the only way the page talks to the core.
//
//   bc.call(cmd, params)   -> Promise of the dispatch reply {ok, ...} / {ok:false, error}; never rejects
//   bc.req(cmd, params)    -> same, rejects with Error(reply.error) when ok is false
//   bc.on(pattern, fn)     -> fn(topic, payload) for every event matching ("displaypad.*", "*"); returns off()
//   bc.image(path, size)   -> Promise of a data URL or null
//   bc.pickFile({title, kind}) -> Promise of a path or null
//   bc.openExternal(url)   -> opens http(s) URLs in the system browser
//   bc.win.minimize() / toggleMaximize() / close()
//
// Transports: pywebview (window.pywebview.api), devserver (HTTP + EventSource), mock (?mock).
// The HTTP transport also serves the phone page (remote.html): a 401 from any /api/ request
// (the phone is not paired, or no longer) fires the window event "bc:unpaired". A closed event
// stream is asked once with HEAD whether that is why; otherwise it connects again, and fires
// "bc:reconnected" when it is back (events may have been missed in between).
// Events arrive as batches, in order: [topic, payload] pairs from the mock, {topic, payload}
// objects from the Python side (devserver SSE, and the "bc:events" DOM event in the window).

const subs = new Set();

function patternToRe(p) {
  if (p === "*") return /^/;
  const esc = p.replace(/[.+?^${}()|[\]\\]/g, "\\$&").replace(/\*/g, ".*");
  return new RegExp("^" + esc + "$");
}

function emit(batch) {
  if (!Array.isArray(batch)) return;
  for (const ev of batch) {
    let topic, payload;
    if (Array.isArray(ev)) [topic, payload] = ev;
    else if (ev && typeof ev === "object") ({ topic, payload } = ev);
    if (typeof topic !== "string") continue;
    for (const s of [...subs]) {
      if (s.re.test(topic)) {
        try { s.fn(topic, payload); } catch (e) { console.error("event handler", topic, e); }
      }
    }
  }
}

const fail = (e) => ({ ok: false, error: String((e && e.message) || e) });

// ── transports: each returns {call, image, pickFile, open, win, name} ──────

function pywebviewTransport() {
  const api = () => window.pywebview.api;
  // The Python side names these; try the likely spellings so a small rename does not break the window.
  const first = (names, ...args) => {
    for (const n of names) if (typeof api()[n] === "function") return api()[n](...args);
    return Promise.resolve(null);
  };
  return {
    name: "pywebview",
    call: (cmd, params) => api().call(cmd, params || {}),
    image: (path, size) => first(["image"], path, size),
    pickFile: (opts) => first(["pick_file", "pickFile"], opts || {}),
    open: (url) => first(["open_external", "openExternal"], url),
    win: {
      minimize: () => first(["win_minimize", "minimize"]),
      toggleMaximize: () => first(["win_toggle_maximize", "toggle_maximize", "toggleMaximize"]),
      close: () => first(["win_close", "close"]),
    },
  };
}

const unpaired = () => window.dispatchEvent(new Event("bc:unpaired"));

function devserverTransport() {
  const api = async (url, opts) => {
    const r = await fetch(url, opts);
    if (r.status === 401) unpaired();
    return r;
  };
  const post = async (url, body) => {
    const r = await api(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    return r.json();
  };
  let es = null, opened = false, probing = false;
  const connect = () => {
    es = new EventSource("/api/events");
    es.onopen = () => {
      if (opened) window.dispatchEvent(new Event("bc:reconnected"));
      opened = true;
    };
    es.onmessage = (m) => { try { emit(JSON.parse(m.data)); } catch (e) { console.error("bad event", e); } };
    // EventSource retries a dropped stream by itself; an answer other than 200 (401, 503) closes it
    // for good. Asked with HEAD /api/call (not /api/events: the devserver would open a stream):
    // 401 means not paired and it stays closed, anything else connects again a little later.
    es.onerror = () => {
      if (es.readyState !== 2 || probing) return;
      probing = true;
      api("/api/call", { method: "HEAD" })
        .then((r) => r.status !== 401, () => true)
        .then((again) => { probing = false; if (again) setTimeout(connect, 3000); });
    };
  };
  connect();
  return {
    name: "devserver",
    call: (cmd, params) => post("/api/call", { cmd, params: params || {} }),
    image: async (path, size) => {
      const r = await api("/api/image?path=" + encodeURIComponent(path) + "&size=" + encodeURIComponent(size || 128));
      const j = await r.json();
      return (j && j.url) || null;
    },
    pickFile: async (opts) => { const j = await post("/api/pick_file", opts || {}); return (j && (j.path !== undefined ? j.path : j)) || null; },
    open: (url) => post("/api/open", { url }),
    win: { minimize() {}, toggleMaximize() {}, close() {} },
  };
}

async function mockTransport() {
  const m = await import("./bridge-mock.js");
  return m.createMock(emit);
}

function waitForPywebview(ms) {
  return new Promise((resolve) => {
    if (window.pywebview && window.pywebview.api) return resolve(true);
    const done = (v) => { window.removeEventListener("pywebviewready", onReady); clearTimeout(timer); resolve(v); };
    const onReady = () => done(true);
    const timer = setTimeout(() => done(false), ms);
    window.addEventListener("pywebviewready", onReady);
  });
}

let transport = null;
const ready = (async () => {
  if (new URLSearchParams(location.search).has("mock")) transport = await mockTransport();
  else if (await waitForPywebview(window.pywebview ? 3000 : 400)) {
    transport = pywebviewTransport();
    // The window delivers core events as a DOM event; it starts sending them once the page says it is ready.
    window.addEventListener("bc:events", (e) => emit(e.detail));
    try { await window.pywebview.api.ready(); } catch (e) { console.error("bridge ready", e); }
  }
  else transport = devserverTransport();
  return transport.name;
})();

const bc = {
  ready,
  get transport() { return transport && transport.name; },
  async call(cmd, params) {
    await ready;
    try {
      const r = await transport.call(cmd, params || {});
      return r && typeof r === "object" ? r : { ok: false, error: "bad reply" };
    } catch (e) { return fail(e); }
  },
  async req(cmd, params) {
    const r = await bc.call(cmd, params);
    if (!r.ok) throw new Error(r.error || "error");
    return r;
  },
  on(pattern, fn) {
    const s = { re: patternToRe(pattern), fn };
    subs.add(s);
    return () => subs.delete(s);
  },
  async image(path, size) {
    if (!path || typeof path !== "string") return null;
    await ready;
    try { return (await transport.image(path, size || 128)) || null; } catch (e) { return null; }
  },
  async pickFile(opts) {
    await ready;
    try { return (await transport.pickFile(opts || {})) || null; } catch (e) { return null; }
  },
  async openExternal(url) {
    if (!/^https?:\/\//i.test(url || "")) return;
    await ready;
    try { await transport.open(url); } catch (e) { /* ignore */ }
  },
  win: {
    minimize: () => ready.then(() => transport.win.minimize()),
    toggleMaximize: () => ready.then(() => transport.win.toggleMaximize()),
    close: () => ready.then(() => transport.win.close()),
  },
  // Called by the Python side (pywebview) with a batch of [topic, payload] pairs.
  _emit: emit,
};

window.bc = bc;
export default bc;
