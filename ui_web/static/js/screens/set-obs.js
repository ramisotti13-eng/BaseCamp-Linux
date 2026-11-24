// OBS: connection settings, connect / disconnect, and the state and scenes from obs.changed.
import bc from "../bridge.js";
import { t } from "../i18n.js";
import { h, clear, toast, makeScope } from "../ui.js";
import { frame, panel, errText } from "./set-common.js";

let scope = null;

export async function mount(el) {
  scope = makeScope();
  const fr = frame(el, "obs", false);
  const S = { status: "disconnected", connected: false, scenes: [], message: "", passwordSet: false };

  const host = h("input", { class: "field", id: "obs-host", type: "text", autocomplete: "off", spellcheck: "false" });
  const port = h("input", { class: "field", id: "obs-port", type: "number", min: 1, max: 65535, step: 1 });
  const pass = h("input", { class: "field", id: "obs-pass", type: "password", autocomplete: "new-password" });
  const stateHost = h("div", { class: "status", role: "status" });
  const scenesHost = h("div");
  const connectBtn = h("button", { class: "btn primary", type: "button" }, t("obs_connect"));
  const disconnectBtn = h("button", { class: "btn", type: "button" }, t("obs_disconnect"));

  function stateText() {
    switch (S.status) {
      case "connecting": return t("obs_connecting");
      case "connected": return t("obs_connected", { n: S.scenes.length });
      case "unreachable": return t("obs_unreachable");
      case "error": return t("obs_error", { msg: S.message || "?" });
      default: return t("obs_disconnected");
    }
  }

  function render() {
    clear(stateHost);
    const warn = S.status === "unreachable" || S.status === "error";
    stateHost.append(h("span", { class: "chip" + (warn ? " warn" : "") },
      h("span", { class: "dot " + (S.connected ? "ok" : warn ? "bad" : "off"), "aria-hidden": "true" }), stateText()));
    connectBtn.disabled = S.status === "connecting";
    disconnectBtn.disabled = !S.connected && S.status !== "connecting";
    clear(scenesHost);
    if (S.connected) {
      scenesHost.append(h("div", { class: "lab", style: "margin-top:22px" }, t("obs_scene")),
        S.scenes.length
          ? h("ul", { class: "scenes" }, S.scenes.map((s) => h("li", null, s)))
          : h("div", { class: "help" }, t("obs_none")));
    }
  }

  async function connect() {
    const params = { host: host.value.trim(), port: Number(port.value) || 4455 };
    if (pass.value) params.password = pass.value;
    const r = await bc.call("obs.connect", params);
    if (!r.ok) return toast(errText(r), "err");
    S.status = "connecting";
    if (pass.value) { S.passwordSet = true; pass.value = ""; pass.placeholder = "••••••••"; }
    render();
  }

  async function disconnect() {
    const r = await bc.call("obs.disconnect");
    if (!r.ok) return toast(errText(r), "err");
    toast(t("settings_saved"), "ok");
  }

  connectBtn.addEventListener("click", connect);
  disconnectBtn.addEventListener("click", disconnect);
  for (const f of [host, port, pass]) f.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); connect(); } });

  fr.main.append(stateHost, panel(t("obs_title"),
    h("div", { class: "help", style: "margin:14px 0 0" }, t("set_obs_help")),
    h("div", { class: "grid2" },
      h("div", null, h("label", { class: "lab", for: "obs-host" }, t("obs_host_label").replace(/:$/, "")), host),
      h("div", null, h("label", { class: "lab", for: "obs-port" }, t("obs_port_label").replace(/:$/, "")), port),
      h("div", { class: "wide" }, h("label", { class: "lab", for: "obs-pass" }, t("obs_password").replace(/:$/, "")), pass,
        h("div", { class: "help" }, t("set_obs_password_help")))),
    h("div", { class: "actions" }, connectBtn, disconnectBtn),
    scenesHost));

  scope.on("obs.changed", (_t, p) => {
    p = p || {};
    S.status = p.status || (p.connected ? "connected" : "disconnected");
    S.connected = !!p.connected;
    S.scenes = Array.isArray(p.scenes) ? p.scenes : [];
    S.message = p.message || "";
    render();
  });

  render();
  const [cfg, st] = await Promise.all([bc.call("obs.config"), bc.call("obs.state")]);
  if (!scope.alive) return;
  if (cfg.ok) {
    host.value = cfg.host || "localhost";
    port.value = cfg.port || 4455;
    S.passwordSet = !!cfg.password_set;
    if (S.passwordSet) pass.placeholder = "••••••••";
  } else { host.value = "localhost"; port.value = 4455; }
  if (st.ok && S.status !== "connecting") {
    S.connected = !!st.connected;
    S.scenes = st.scenes || [];
    S.status = S.connected ? "connected" : "disconnected";
  }
  render();
}

export function unmount() { if (scope) scope.dispose(); scope = null; }
