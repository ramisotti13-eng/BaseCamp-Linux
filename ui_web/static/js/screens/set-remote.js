// Settings > Remote access: phones in the LAN pair with this PC and press its pads. The switch and
// the port, the two boxes (shell commands from phones, presses while the PC is locked), "Pair a
// device" with the QR code of the first address, and the paired devices.
//
// Device names come from the phones: they are only ever text nodes (h() and toast() make text).
import bc from "../bridge.js";
import { t, currentLang } from "../i18n.js";
import { h, append, clear, toast, makeScope, confirmDialog, promptDialog } from "../ui.js";
import { frame, panel, errText, iconBtn } from "./set-common.js";
import { qrMatrix, qrCanvas } from "../qr.js";

let scope = null;

const PAIR_MS = 120000;           // a code is valid for 2 minutes (core/remote/pairing.py)
const QR_CSS = 168;               // the QR code's largest side on the page, quiet zone included

function clock(ms) {
  const s = Math.max(0, Math.ceil(ms / 1000));
  return Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0");
}

function when(secs) {
  if (!secs) return "?";
  const d = new Date(secs * 1000);
  try {
    return d.toLocaleString(currentLang(), { dateStyle: "medium", timeStyle: "short" });
  } catch (e) {
    return d.toLocaleString();
  }
}

/** The QR code of `url` as a canvas at a whole number of pixels per module. */
function qrImage(url) {
  let m;
  try { m = qrMatrix(url); } catch (e) { return null; }
  const n = m.length + 8;
  const css = Math.max(2, Math.floor(QR_CSS / n));
  const ratio = Math.max(1, Math.ceil(window.devicePixelRatio || 1));
  const canvas = qrCanvas(m, css * ratio);
  canvas.className = "rqr";
  canvas.style.width = canvas.style.height = n * css + "px";
  canvas.setAttribute("role", "img");
  canvas.setAttribute("aria-label", t("rset_qr_label", { url }));
  return canvas;
}

export async function mount(el) {
  scope = makeScope();
  const fr = frame(el, "remote", false);
  // settings.server_error: why the server could not listen, kept by the core (also from before
  // this page opened), or null; a remote.error event sets it while the page is open.
  const S = { settings: null, devices: [], pairing: null };

  // ── access: switch, port, the two boxes ────────────────────────────────────
  const accessHost = h("div");
  const port = h("input", { class: "field", id: "rset-port", type: "number", min: 1024, max: 65535, step: 1 });

  async function save(params, revert) {
    const r = await bc.call("remote.set_settings", params);
    if (!scope.alive) return null;
    if (!r.ok) { toast(errText(r), "err"); if (revert) revert(); drawAccess(); return null; }
    S.settings = r;
    drawAccess();
    drawRight();
    return r;
  }

  function check(id, label, help, checked, fn) {
    const box = h("input", { type: "checkbox", id, checked, "aria-describedby": help ? id + "-help" : null });
    box.addEventListener("change", () => fn(box.checked));
    return h("div", { class: "rbox" }, h("label", { class: "check", for: id }, box, h("span", null, label)),
      help ? h("div", { class: "help", id: id + "-help" }, help) : null);
  }

  function errorLine() {
    const e = S.settings && S.settings.server_error;
    if (!e || !S.settings.enabled) return null;
    const text = e.code === "port_in_use" ? t("rset_port_in_use", { port: e.port })
      : t("rset_cannot_listen", { port: e.port, err: e.error || "?" });
    return h("div", { class: "msg err", role: "alert" }, h("span", null, text, " ",
      h("button", { class: "linkbtn", type: "button", onclick: retry }, t("rset_try_again"))));
  }

  function drawAccess() {
    const s = S.settings;
    clear(accessHost);
    if (!s) return;
    if (document.activeElement !== port) port.value = s.port;
    append(accessHost, [
      check("rset-enabled", t("rset_enabled"), t("rset_intro"), !!s.enabled, (v) => save({ enabled: v })),
      h("div", { class: "rport" },
        h("label", { class: "lab", for: "rset-port" }, t("rset_port")),
        h("div", { class: "rportrow" }, port, errorLine())),
      h("p", { class: "help rnote" }, t("rset_unencrypted")),
      check("rset-shell", t("rset_allow_shell"), t("rset_allow_shell_help"), !!s.allow_shell,
        async (v) => {
          // On: the risk once more, in a dialog. Off needs no question.
          if (v) {
            const ok = await confirmDialog({ title: t("rset_shell_confirm_title"), message: t("rset_allow_shell_help"),
              okLabel: t("rset_shell_allow"), danger: true });
            if (!scope.alive) return;
            if (!ok) { drawAccess(); return; }
          }
          save({ allow_shell: v });
        }),
      check("rset-locked", t("rset_allow_locked"), t("rset_allow_locked_help"), !!s.allow_locked,
        (v) => save({ allow_locked: v })),
      s.enabled && !s.allow_locked && !s.lock_watching
        ? h("div", { class: "msg warn", role: "status" }, t("rset_lock_unwatched")) : null]);
  }

  port.addEventListener("change", () => {
    const want = Number(port.value);
    const was = S.settings ? S.settings.port : null;
    if (!port.value || want === was) { if (was != null) port.value = was; return; }
    if (S.pairing) endPairing();        // its addresses name the old port
    save({ port: want }, () => { if (was != null) port.value = was; });
  });
  port.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); port.blur(); } });

  // The server could not have the port: the same settings again make it try again
  // (ui_web/remote_server.py RemoteHost follows remote.changed).
  function retry() {
    if (!S.settings) return;
    S.settings.server_error = null;     // the reply says so too; RemoteHost sets it again if it fails
    drawAccess();
    save({ enabled: true, port: S.settings.port });
  }

  // ── right column: pairing, or the devices ──────────────────────────────────
  const rightHost = h("div");
  let ticker = null;

  function stopTicker() { if (ticker != null) { scope.cancel(ticker); ticker = null; } }

  async function pair() {
    const r = await bc.call("remote.pair");
    if (!scope.alive) return;
    if (!r.ok) { toast(errText(r), "err"); return; }
    const left = Number(r.expires) * 1000 - Date.now();
    S.pairing = { code: String(r.code || ""), urls: Array.isArray(r.urls) ? r.urls.map(String) : [],
      deadline: Date.now() + (left > 0 && left <= PAIR_MS ? left : PAIR_MS),
      known: new Set(S.devices.map((d) => d.id)) };
    drawRight();
  }

  function endPairing() {
    stopTicker();
    S.pairing = null;
    drawRight();
  }

  function drawPairing() {
    const p = S.pairing;
    const left = p.deadline - Date.now();
    if (left <= 0) {
      return [h("div", { class: "msg note", role: "status" }, t("rset_expired")),
        h("div", { class: "actions" },
          h("button", { class: "btn primary", type: "button", onclick: pair }, t("rset_pair")),
          h("button", { class: "btn", type: "button", onclick: endPairing }, t("rset_pair_done")))];
    }
    const timer = h("div", { class: "rleft", role: "timer" }, t("rset_expires_in", { time: clock(left) }));
    const tick = () => {
      ticker = null;
      if (!S.pairing || S.pairing !== p) return;
      const rest = p.deadline - Date.now();
      if (rest <= 0) { drawRight(); return; }
      timer.textContent = t("rset_expires_in", { time: clock(rest) });
      ticker = scope.later(tick, 1000 - (Date.now() % 1000) + 20);
    };
    stopTicker();
    ticker = scope.later(tick, 1000 - (Date.now() % 1000) + 20);
    const code = p.code.length === 8 ? p.code.slice(0, 4) + " " + p.code.slice(4) : p.code;
    const img = p.urls.length ? qrImage(p.urls[0]) : null;
    return [
      h("div", { class: "rpair" },
        img,
        h("div", { class: "rpairside" },
          h("div", { class: "lab" }, t("rset_code")),
          h("div", { class: "rcode", "aria-label": p.code.split("").join(" ") }, code),
          timer,
          h("p", { class: "help" }, t("rset_pair_how")))),
      h("div", { class: "lab rlab" }, t("rset_addresses")),
      p.urls.length
        ? h("ul", { class: "rurls mono" }, p.urls.map((u) => h("li", null, u)))
        : h("div", { class: "help" }, t("rset_no_address")),
      h("p", { class: "help" }, t("rset_firewall_hint", { port: S.settings ? S.settings.port : 8780 })),
      h("p", { class: "help" }, t("rset_address_hint")),
      h("div", { class: "actions" }, h("button", { class: "btn", type: "button", onclick: endPairing }, t("rset_pair_done"))),
    ];
  }

  function deviceRow(d) {
    const name = d.name || "?";
    return h("li", { class: "rdev" },
      h("div", { class: "txt" },
        h("div", { class: "nm", title: name }, name),
        h("div", { class: "sub" }, t("rset_device_dates", { paired: when(d.paired), seen: when(d.last_seen) }))),
      iconBtn("pencil", t("rset_rename", { name }), () => rename(d)),
      iconBtn("trash", t("rset_remove", { name }), () => remove(d)));
  }

  function drawDevices() {
    const on = !!(S.settings && S.settings.enabled);
    return [
      h("div", { class: "actions rpairbtn" },
        h("button", { class: "btn primary", type: "button", disabled: !on, onclick: pair }, t("rset_pair"))),
      on ? null : h("div", { class: "help" }, t("rset_pair_off")),
      S.devices.length
        ? h("ul", { class: "rdevs" }, S.devices.map(deviceRow))
        : h("div", { class: "help" }, t("rset_no_devices")),
    ];
  }

  function drawRight() {
    stopTicker();
    clear(rightHost);
    if (S.pairing && !(S.settings && S.settings.enabled)) S.pairing = null;
    append(rightHost, S.pairing ? drawPairing() : drawDevices());
    rightPanel.querySelector("h2").textContent = S.pairing ? t("rset_pair") : t("rset_devices");
  }

  async function rename(d) {
    const name = await promptDialog({ title: t("rset_rename_title"), label: t("rset_rename_prompt"), value: d.name || "" });
    if (!name || !scope.alive) return;
    const r = await bc.call("remote.rename_device", { id: d.id, name });
    if (!r.ok) toast(errText(r), "err");
  }

  async function remove(d) {
    const ok = await confirmDialog({ title: t("rset_remove_title"), message: t("rset_remove_msg", { name: d.name || "?" }),
      okLabel: t("rset_remove_btn"), danger: true });
    if (!ok || !scope.alive) return;
    const r = await bc.call("remote.remove_device", { id: d.id });
    if (!r.ok) toast(errText(r), "err");
  }

  async function loadDevices() {
    const r = await bc.call("remote.devices");
    if (!scope.alive || !r.ok) return;
    S.devices = Array.isArray(r.devices) ? r.devices : [];
    // A phone used the code (it is good for one device): back to the list, with the new one.
    const p = S.pairing;
    const fresh = p ? S.devices.filter((d) => !p.known.has(d.id)) : [];
    if (fresh.length) {
      S.pairing = null;
      toast(t("rset_paired_toast", { name: fresh[0].name || "?" }), "ok");
    }
    if (!S.pairing) drawRight();
  }

  async function loadSettings() {
    const r = await bc.call("remote.settings");
    if (!scope.alive) return;
    if (!r.ok) { toast(errText(r), "err"); return; }
    S.settings = r;
    drawAccess();
    if (!S.pairing || !r.enabled) drawRight();
  }

  // ── the page ───────────────────────────────────────────────────────────────
  const rightPanel = panel(t("rset_devices"), rightHost);
  fr.main.append(h("div", { class: "cols2 rset" },
    h("div", { class: "stack" }, panel(t("rset_title"), accessHost)),
    h("div", { class: "stack" }, rightPanel)));

  scope.on("remote.changed", () => { loadSettings(); loadDevices(); });
  scope.on("remote.device_removed", (_t, p) => {
    const id = p && p.id;
    if (id) { S.devices = S.devices.filter((d) => d.id !== id); if (!S.pairing) drawRight(); }
  });
  scope.on("remote.error", (_t, p) => {
    p = p || {};
    if (!S.settings) return;            // loadSettings() brings it (the core keeps it)
    S.settings.server_error = { code: p.code === "port_in_use" ? "port_in_use" : "cannot_listen", port: p.port, error: p.error };
    drawAccess();
  });

  drawRight();
  await Promise.all([loadSettings(), loadDevices()]);
}

export function unmount() { if (scope) scope.dispose(); scope = null; }
