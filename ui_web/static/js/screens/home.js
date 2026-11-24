// Home: a card per plugged-in device, two banners, footer (GitHub and Ko-fi). Devices that are not plugged in are left out.
import bc from "../bridge.js";
import { t } from "../i18n.js";
import * as devices from "../devices.js";
import { artFor } from "../art.js";
import { photoHint } from "../photo-hint.js";
import { h, icon, clear, makeScope } from "../ui.js";

const REPO = "https://github.com/ramisotti13-eng/BaseCamp-Linux";
const LINKS = {
  help: REPO + "/issues",
  plugins: "#/settings/plugins",   // the application's own Plugins screen
  kofi: "https://ko-fi.com/D1D61WIJRD",
};

let scope = null;

function card(d) {
  const present = devices.isPresent(d.id);
  const eyebrow = t("home_type_" + d.type) + (present ? "" : " · " + t("home_not_connected"));
  const cta = present
    ? h("a", { class: "cta", href: d.route }, icon("wrench"), t("home_customize"), h("span", { class: "sr-only" }, " " + d.name))
    : h("button", { class: "cta", type: "button", "aria-disabled": "true", title: t("home_not_connected"), onclick: (e) => e.preventDefault() },
        icon("wrench"), t("home_customize"), h("span", { class: "sr-only" }, " " + d.name));
  const el = h("section", { class: "dev" + (present ? "" : " off"), "aria-label": d.name },
    h("div", { class: "acc" }), h("div", { class: "eyebrow h" }, eyebrow), h("h2", { class: "name h" }, d.name), cta);
  // the picture goes first so the button stays on top
  const svg = mkSvg(d.id);
  el.insertBefore(svg, cta);
  const hint = photoHint(d.id, "home");          // our drawing: offer Base Camp's photo
  if (hint) el.append(hint);
  devices.photoUrl(d.id, "home", 700).then((url) => {
    if (!url || !svg.isConnected) return;
    const img = h("img", { class: svg.getAttribute("class"), src: url, alt: "", "aria-hidden": "true" });
    svg.replaceWith(img);
  });
  return el;
}

// No DisplayPad plugged in: a virtual pad (keys on the phone, under the DisplayPad's tab) can stand in.
const VPAD_ART =
  '<svg viewBox="0 0 120 200" aria-hidden="true"><rect x="6" y="4" width="108" height="192" rx="16" fill="#23262f" stroke="#5d6375" stroke-width="2"/>' +
  '<rect x="16" y="22" width="88" height="150" rx="4" fill="#101217"/>' +
  [0, 1, 2, 3].map((r) => [0, 1, 2].map((c) => `<rect x="${22 + c * 27}" y="${32 + r * 34}" width="22" height="22" rx="3" fill="${(r + c) % 3 ? "#3a3f4d" : "#0038e2"}"/>`).join("")).join("") +
  '<rect x="48" y="182" width="24" height="4" rx="2" fill="#5d6375"/></svg>';

function vpadCard() {
  const tpl = document.createElement("template");
  tpl.innerHTML = VPAD_ART;   // our own static drawing, no user data
  const svg = tpl.content.firstChild;
  svg.setAttribute("class", "pic");
  return h("section", { class: "dev vpcard", "aria-label": t("vp_home_title") },
    h("div", { class: "acc" }), h("div", { class: "eyebrow h" }, t("vp_home_eyebrow")), h("h2", { class: "name h" }, t("vp_home_title")),
    svg,
    h("a", { class: "cta", href: "#/displaypad/pads" }, icon("plus"), t("vp_home_btn")));
}

function mkSvg(id) {
  const tpl = document.createElement("template");
  tpl.innerHTML = artFor(id).trim();   // our own static drawings, no user data
  const svg = tpl.content.firstChild;
  // Sized by the card (app.css): keyboards are wide, the mouse sits a little further in.
  svg.setAttribute("class", "pic" + (id === "everest_max" || id === "everest60" ? " wide" : id === "makalu67" ? " mouse" : ""));
  return svg;
}

function banner(cls, eyebrow, title, url, cta) {
  // "#/..." is a screen of the application, anything else opens in the browser.
  const inApp = url.startsWith("#");
  return h("a", { class: "banner " + cls, href: url, onclick: inApp ? null : (e) => { e.preventDefault(); bc.openExternal(url); } },
    h("div", { class: "bar" }), h("div", { class: "e h" }, eyebrow), h("div", { class: "t h" }, title),
    cta ? h("span", { class: "cta", "aria-hidden": "true" }, cta) : null);
}

/** A small icon link in the footer, opened in the browser. */
function footLink(url, label, ic) {
  const i = icon(ic);
  i.setAttribute("width", 20);
  i.setAttribute("height", 20);
  return h("a", { href: url, "aria-label": label, title: label, onclick: (e) => { e.preventDefault(); bc.openExternal(url); } }, i);
}

function render(el, version) {
  clear(el);
  const list = devices.DEVICES.filter((d) => devices.isPresent(d.id));
  const cards = list.map(card);
  if (!devices.isPresent("displaypad")) cards.push(vpadCard());
  el.append(h("div", { class: "home" },
    list.length ? null : h("p", { class: "home-empty" }, t("home_no_devices")),
    h("div", { class: "devices" }, cards),
    h("div", { class: "banners" },
      banner("b1", t("home_help_eyebrow"), t("home_help_title"), LINKS.help),
      banner("b2", t("home_plugins_eyebrow"), t("home_plugins_title"), LINKS.plugins, [icon("wrench"), t("home_plugins_btn")])),
    h("div", { class: "foot" },
      footLink(REPO, "GitHub", "github"),
      footLink(LINKS.kofi, t("home_kofi_title"), "cup"),
      h("span", { class: "ver" }, version ? t("home_version", { ver: version }) : ""))));
}

async function getVersion() {
  for (const cmd of ["control.ping", "app.version"]) {
    const r = await bc.call(cmd);
    if (r && r.ok) { const v = r.version || r.result; if (typeof v === "string") return v; }
  }
  return "";
}

export async function mount(el) {
  scope = makeScope();
  let version = "";
  render(el, version);
  scope.add(devices.onChange(() => render(el, version)));
  scope.add(devices.onPhotos(() => render(el, version)));
  version = await getVersion();
  if (scope.alive) render(el, version);
}

export function unmount() { if (scope) scope.dispose(); scope = null; }
