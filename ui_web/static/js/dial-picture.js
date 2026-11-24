// A close-up of the Everest Max's Display Dial for the Display Dial page, drawn by us: the round
// display on the brushed silver case corner with the dial menu (icons around, the entry's name in the
// middle, "MENU" below, an arc in the menu colour at the top) and the keys under it.
//
//   const pic = createDialPicture(host, { onPick(menu) });
//   pic.update({ menus: {clock: true, ...}, color: "#f3cc23" })    // follows the keyboard's settings
//
// The menu's name on the display is the dial's own text (always English), so it is not translated.
import { esc } from "./ui.js";

// The menus in the order around the display, clockwise from the upper left; the icons are the
// symbols of index.html. The names are what the dial prints.
const MENUS = [
  ["clock", "dd-clock", "CLOCK"], ["profile", "dd-profile", "PROFILE"], ["rgb", "bulb", "RGB"], ["volume", "dd-volume", "VOLUME"],
  ["brightness", "sun", "BRIGHTNESS"], ["pcinfo", "monitor", "PC INFO"], ["apm", "dd-apm", "APM"], ["custom", "keys", "CUSTOM"],
];
const KEYS = [["PRT SCN", "SCR LK", "PAUSE"], ["INSERT", "HOME", "PG UP"]];
const CX = 180, CY = 172, R_BEZEL = 142, R_SCREEN = 126, R_ICON = 88, R_ARC = 116, ICON = 28;
const rad = (deg) => (deg * Math.PI) / 180;

function arcPath(r, from, to) {
  const p = (a) => (CX + r * Math.cos(rad(a))).toFixed(1) + " " + (CY + r * Math.sin(rad(a))).toFixed(1);
  return `M${p(from)} A${r} ${r} 0 0 1 ${p(to)}`;
}

function keyCaps() {
  let out = "";
  KEYS.forEach((row, ri) => row.forEach((legend, ci) => {
    const x = 26 + ci * 108, y = 332 + ri * 50;
    out += `<g><rect x="${x}" y="${y}" width="96" height="40" rx="6" fill="#1d1d1d" stroke="#0c0c0c"/>`
      + `<rect x="${x + 3}" y="${y + 3}" width="90" height="29" rx="4" fill="#232323"/>`
      + `<text x="${x + 10}" y="${y + 17}" font-size="11" fill="#e8e8e8" opacity=".92">${esc(legend)}</text></g>`;
  }));
  return out;
}

const DEFS = `<defs>
<pattern id="dd-brush" width="4" height="6" patternUnits="userSpaceOnUse">
  <rect width="4" height="1" fill="#fff" opacity=".11"/><rect y="3" width="4" height="1" fill="#000" opacity=".07"/></pattern>
<linearGradient id="dd-case" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#b6b7b9"/><stop offset=".4" stop-color="#9e9fa1"/><stop offset="1" stop-color="#909193"/></linearGradient>
<linearGradient id="dd-sheen" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#000" stop-opacity=".07"/><stop offset=".22" stop-color="#fff" stop-opacity=".12"/>
  <stop offset=".48" stop-color="#000" stop-opacity=".05"/><stop offset=".74" stop-color="#fff" stop-opacity=".09"/><stop offset="1" stop-color="#000" stop-opacity=".08"/></linearGradient>
<radialGradient id="dd-bezel" cx=".5" cy=".4" r=".7"><stop offset="0" stop-color="#4a4b4e"/><stop offset=".75" stop-color="#232426"/><stop offset="1" stop-color="#111214"/></radialGradient>
</defs>`;

/** The picture as SVG markup for the state; `current` is the entry whose name shows in the middle. */
function markup(state, current) {
  const color = /^#[0-9a-f]{6}$/i.test(state.color) ? state.color : "#f3cc23";
  const menus = state.menus || {};
  let icons = "";
  MENUS.forEach(([id, sym], i) => {
    const a = -112.5 + 45 * i;
    const x = CX + R_ICON * Math.cos(rad(a)) - ICON / 2, y = CY + R_ICON * Math.sin(rad(a)) - ICON / 2;
    const on = !!menus[id], here = id === current;
    icons += `<g class="ddp-icon" data-menu="${id}" opacity="${on ? 1 : 0.28}" style="color:${here ? color : "#d4d6da"}${on ? ";cursor:pointer" : ""}">`
      + `<circle cx="${x + ICON / 2}" cy="${y + ICON / 2}" r="${ICON / 2 + 4}" fill="${here ? "#1b1c1f" : "transparent"}"/>`
      + `<use href="#i-${sym}" x="${x}" y="${y}" width="${ICON}" height="${ICON}"/></g>`;
  });
  const name = (MENUS.find((m) => m[0] === current) || [0, 0, ""])[2];
  return `<svg class="ddpic-svg" viewBox="0 0 360 430" role="img" aria-label="Display Dial" font-family="inherit">${DEFS}
<rect x="1" y="1" width="358" height="428" rx="26" fill="url(#dd-case)" stroke="#6c6e72"/>
<rect x="1" y="1" width="358" height="428" rx="26" fill="url(#dd-brush)"/>
<rect x="1" y="1" width="358" height="428" rx="26" fill="url(#dd-sheen)"/>
<circle cx="${CX}" cy="${CY}" r="${R_BEZEL}" fill="url(#dd-bezel)" stroke="#0a0a0b" stroke-width="2"/>
<circle cx="${CX}" cy="${CY}" r="${R_SCREEN}" fill="#07080a" stroke="#2a2b2e" stroke-width="2"/>
<path d="${arcPath(R_ARC, -140, -40)}" fill="none" stroke="${color}" stroke-width="5" stroke-linecap="round"/>
${icons}
<text x="${CX}" y="${CY + 4}" text-anchor="middle" font-size="25" font-weight="700" fill="#fff" letter-spacing="1">${esc(name)}</text>
<text x="${CX}" y="${CY + 30}" text-anchor="middle" font-size="12" fill="#9a9da3" letter-spacing="3">MENU</text>
${keyCaps()}</svg>`;
}

export function createDialPicture(host, { onPick } = {}) {
  let state = { menus: {}, color: "#f3cc23" };
  let picked = null;
  const firstOn = () => (MENUS.find(([id]) => state.menus[id]) || [null])[0];
  const current = () => (picked && state.menus[picked] ? picked : firstOn());
  function paint() {
    // Our own markup; the only dynamic parts are escaped or validated in markup().
    host.innerHTML = markup(state, current());
  }
  host.addEventListener("click", (e) => {
    const g = e.target.closest && e.target.closest(".ddp-icon");
    if (!g || !state.menus[g.dataset.menu]) return;
    picked = g.dataset.menu;
    paint();
    if (onPick) onPick(picked);
  });
  paint();
  return { update(next) { state = { ...state, ...next }; paint(); } };
}
