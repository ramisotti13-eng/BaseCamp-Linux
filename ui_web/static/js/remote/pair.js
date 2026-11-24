// Pairing the phone: the code from the address (/pair#CODE, what the QR code on the PC holds) is
// posted at once; without one, or when it was refused, a field takes the code shown on the PC.
// The server answers with the cookie; the page then starts again at "/".
import { t } from "../i18n.js";
import { h, clear } from "../ui.js";

/** POST /api/pair {code}: {ok: true} or {ok: false, error, status}. Never rejects. */
async function redeem(code) {
  try {
    const r = await fetch("/api/pair", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code }) });
    let body = null;
    try { body = await r.json(); } catch (e) { body = null; }
    if (r.ok && body && body.ok) return { ok: true };
    return { ok: false, status: r.status, error: (body && body.error) || "HTTP " + r.status };
  } catch (e) {
    return { ok: false, status: 0, error: String((e && e.message) || e) };
  }
}

const cleanCode = (s) => String(s || "").replace(/\s+/g, "").toUpperCase();

/** The pairing screen in `host`. opts: {code: a code to post at once, note: a line above it}. */
export function mountPair(host, { code, note } = {}) {
  const field = h("input", { class: "field code", id: "paircode", type: "text", inputmode: "text",
    autocomplete: "one-time-code", autocapitalize: "characters", autocorrect: "off", spellcheck: "false",
    maxlength: "16", "aria-describedby": "pairmsg" });
  const btn = h("button", { class: "btn primary big", type: "submit" }, t("remote_pair_btn"));
  const msg = h("p", { class: "pairmsg", id: "pairmsg", role: "status", "aria-live": "polite" }, note || "");
  const form = h("form", { class: "pairform", novalidate: true },
    h("label", { class: "lbl", for: "paircode" }, t("remote_pair_code")), field, btn);

  clear(host);
  host.append(h("section", { class: "pair" },
    h("img", { class: "pairlogo", src: "img/logo_mark.png", alt: "" }),
    h("h1", { class: "h" }, t("remote_pair_title")),
    h("p", { class: "dim" }, t("remote_pair_intro")),
    form, msg));

  let busy = false;
  async function submit(value) {
    const c = cleanCode(value);
    if (!c || busy) return;
    busy = true;
    btn.disabled = true; field.disabled = true;
    msg.className = "pairmsg"; msg.textContent = t("remote_pairing");
    const r = await redeem(c);
    if (r.ok) { location.replace("/"); return; }
    busy = false;
    btn.disabled = false; field.disabled = false;
    msg.className = "pairmsg err";
    msg.textContent = r.status === 403 ? t("remote_pair_wrong") : t("remote_pair_failed", { err: r.error });
    field.value = c;
    field.focus();
    field.select();
  }

  form.addEventListener("submit", (e) => { e.preventDefault(); submit(field.value); });
  // The code is not kept in the address (or the history) once read.
  if (location.hash) history.replaceState(null, "", location.pathname + location.search);
  if (cleanCode(code)) submit(code);
  else field.focus();
}
