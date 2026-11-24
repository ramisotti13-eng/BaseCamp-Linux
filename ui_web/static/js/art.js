// Own SVG drawings of the devices, in the logo palette.
// Each returns an SVG string; gradient ids are prefixed so several can share a page.

const KEYCOLORS = ["#0038e2", null, "#fd9d02", "#7c3aed", null, "#16a34a", null, "#e11d48", "#0ea5e9", null, "#0038e2", null];

function displaypad() {
  let cells = "", tiles = "";
  for (let i = 0; i < 12; i++) {
    const x = (i % 6) * 31, y = Math.floor(i / 6) * 31;
    cells += `<rect x="${x}" y="${y}" width="26" height="26" rx="4"/>`;
    if (KEYCOLORS[i]) tiles += `<rect x="${x + 3}" y="${y + 3}" width="20" height="20" rx="3" fill="${KEYCOLORS[i]}"/>`;
  }
  return `<svg viewBox="0 0 260 150" aria-hidden="true"><defs><linearGradient id="dpg" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#3b4050"/><stop offset="1" stop-color="#15171d"/></linearGradient></defs>
<path d="M30 40 L230 22 L250 112 L20 132 Z" fill="url(#dpg)" stroke="#555b6c"/>
<path d="M20 132 L250 112 L250 124 L22 146 Z" fill="#0b0c10"/>
<g transform="translate(44 44) skewY(-5)"><g fill="#0f1116" stroke="#2c3140">${cells}</g>${tiles}</g></svg>`;
}

function makalu() {
  return `<svg viewBox="0 0 260 160" aria-hidden="true"><defs>
<linearGradient id="mkg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#3a3f4d"/><stop offset="1" stop-color="#101217"/></linearGradient>
<linearGradient id="mkr" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#0038e2"/><stop offset=".5" stop-color="#7c3aed"/><stop offset="1" stop-color="#fd9d02"/></linearGradient></defs>
<path d="M70 140 C20 120 20 60 70 30 C110 6 190 10 220 50 C245 85 230 130 180 145 C140 158 100 152 70 140Z" fill="url(#mkg)" stroke="#555b6c"/>
<path d="M95 40 C120 25 165 25 185 45" stroke="url(#mkr)" stroke-width="6" fill="none" stroke-linecap="round"/>
<path d="M140 22 C150 60 150 70 146 84" stroke="#0b0c10" stroke-width="2" fill="none" opacity=".7"/>
<g fill="#0b0c10" opacity=".9"><circle cx="150" cy="95" r="5"/><circle cx="168" cy="105" r="5"/><circle cx="186" cy="95" r="5"/><circle cx="168" cy="85" r="5"/><circle cx="132" cy="105" r="5"/><circle cx="150" cy="115" r="5"/><circle cx="186" cy="115" r="5"/><circle cx="204" cy="105" r="5"/></g></svg>`;
}

function everestMax() {
  let keys = "";
  for (let r = 0; r < 4; r++) for (let c = 0; c < 12; c++)
    keys += `<rect x="${34 + c * 14 + r * 1.6}" y="${52 + r * 12 - c * 1.2}" width="11" height="9" rx="1.5"/>`;
  return `<svg viewBox="0 0 260 140" aria-hidden="true">
<path d="M10 60 L240 30 L252 96 L20 128 Z" fill="#2a2e3a" stroke="#4a5060"/>
<path d="M20 128 L252 96 L252 106 L24 138 Z" fill="#0b0c10"/>
<path d="M30 66 L200 44 L206 80 L36 102Z" fill="#14161c"/><g fill="#2b2f3b">${keys}</g>
<circle cx="226" cy="58" r="9" fill="#0b0c10" stroke="#0038e2" stroke-width="2"/>
<path d="M18 124 L250 94" stroke="#0038e2" stroke-width="2" opacity=".8"/></svg>`;
}

function everest60() {
  let keys = "";
  for (let r = 0; r < 5; r++) for (let c = 0; c < 14; c++)
    keys += `<rect x="${34 + c * 14.4 + r * 1.2}" y="${56 + r * 11 - c * 1.5}" width="11.5" height="8.5" rx="1.5"/>`;
  return `<svg viewBox="0 0 260 140" aria-hidden="true">
<path d="M16 66 L238 34 L250 98 L26 128 Z" fill="#2a2e3a" stroke="#4a5060"/>
<path d="M26 128 L250 98 L250 108 L30 138 Z" fill="#0b0c10"/>
<path d="M28 70 L232 42 L240 94 L34 118Z" fill="#14161c"/><g fill="#2b2f3b">${keys}</g>
<path d="M26 124 L250 94" stroke="#fd9d02" stroke-width="2" opacity=".8"/></svg>`;
}

function macropad() {
  let keys = "";
  for (let i = 0; i < 12; i++) {
    const x = (i % 6) * 29, y = Math.floor(i / 6) * 33;
    keys += `<rect x="${x}" y="${y}" width="24" height="27" rx="4" fill="${i === 1 ? "#0038e2" : i === 8 ? "#fd9d02" : "#0f1116"}" stroke="#2c3140"/>`;
  }
  return `<svg viewBox="0 0 260 150" aria-hidden="true"><defs><linearGradient id="mpg" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#3b4050"/><stop offset="1" stop-color="#15171d"/></linearGradient></defs>
<path d="M130 4 v14" stroke="#111" stroke-width="5"/>
<path d="M30 28 L230 28 L240 112 L20 112 Z" fill="url(#mpg)" stroke="#555b6c"/>
<path d="M20 112 L240 112 L240 124 L20 124 Z" fill="#0b0c10"/>
<g transform="translate(46 40)">${keys}</g></svg>`;
}

/** The MacroPad on its own screen: 560x300; the 6x2 key grid of hotspots.js ("macropad/page") goes over it. */
function macropadPage() {
  return `<svg class="padpic" viewBox="0 0 560 300" aria-hidden="true"><defs>
<linearGradient id="mpp" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#4a4f5e"/><stop offset=".55" stop-color="#23262f"/><stop offset="1" stop-color="#121318"/></linearGradient></defs>
<path d="M280 0 v26" stroke="#111" stroke-width="12"/>
<rect x="10" y="24" width="540" height="262" rx="22" fill="url(#mpp)" stroke="#5d6375"/>
<rect x="30" y="44" width="500" height="206" rx="12" fill="#101217"/>
<path d="M236 268 L324 268 L314 277 L246 277Z" fill="#3a3f4d"/></svg>`;
}

/** Top view of the Makalu 67 or Makalu Max for its own screen. Drawn in the pixel space of Mountain's photo
 *  (364x800 / 778x1400), so js/hotspots.js serves both. The hotspots are a separate layer, never part of this. */
export function makaluTop(isMax) {
  const grads = `<linearGradient id="mtb" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#3f4454"/><stop offset=".6" stop-color="#1d2029"/><stop offset="1" stop-color="#0f1116"/></linearGradient>
<linearGradient id="mtr" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#0038e2"/><stop offset=".55" stop-color="#7c3aed"/><stop offset="1" stop-color="#fd9d02"/></linearGradient>`;
  if (!isMax) {
    return `<svg viewBox="0 0 364 800" aria-hidden="true"><defs>${grads}
<pattern id="mth" width="26" height="26" patternUnits="userSpaceOnUse" patternTransform="rotate(-35)"><rect x="4" y="4" width="8" height="18" rx="4" fill="#0b0c10"/></pattern></defs>
<path d="M192 0 V180" stroke="#1d2028" stroke-width="20"/><path d="M192 0 V180" stroke="#555b6c" stroke-width="20" stroke-dasharray="3 9" opacity=".6"/>
<path d="M192 150 C290 150 330 210 332 300 C334 420 364 520 364 600 C364 720 290 800 192 800 C90 800 0 720 0 600 C0 520 30 420 36 300 C40 210 95 150 192 150Z" fill="url(#mtb)" stroke="#5d6375" stroke-width="3"/>
<path d="M36 430 Q192 412 332 430 L350 520 Q364 560 364 600 C364 720 290 800 192 800 C90 800 0 720 0 600 Q0 560 14 520Z" fill="url(#mth)" opacity=".85"/>
<path d="M192 150 V222 M36 430 Q192 412 332 430" stroke="#0b0c10" stroke-width="3" fill="none" opacity=".8"/>
<rect x="152" y="222" width="80" height="275" rx="40" fill="#0b0c10" stroke="url(#mtr)" stroke-width="7"/>
<rect x="168" y="250" width="48" height="85" rx="14" fill="#2a2e3a" stroke="#4a5060"/><path d="M172 270 H212 M172 285 H212 M172 300 H212 M172 315 H212" stroke="#14161c" stroke-width="3"/>
<g fill="#fd9d02" opacity=".85"><circle cx="182" cy="368" r="5"/><circle cx="203" cy="368" r="5"/><circle cx="182" cy="390" r="5"/><circle cx="203" cy="390" r="5"/></g>
<rect x="168" y="425" width="48" height="50" rx="12" fill="#20232c" stroke="#4a5060"/>
<rect x="20" y="350" width="20" height="55" rx="8" fill="#2a2e3a" stroke="#4a5060"/><rect x="20" y="410" width="20" height="130" rx="9" fill="#2a2e3a" stroke="#4a5060"/></svg>`;
  }
  return `<svg viewBox="0 0 778 1400" aria-hidden="true"><defs>${grads}</defs>
<path d="M389 0 C600 0 748 120 748 290 L752 640 C760 760 778 900 778 1030 C778 1250 620 1400 389 1400 C150 1400 0 1250 0 1030 C0 900 40 780 66 640 L68 290 C68 120 190 0 389 0Z" fill="url(#mtb)" stroke="#5d6375" stroke-width="5"/>
<path d="M412 0 V190 M68 610 Q400 590 752 625" stroke="#0b0c10" stroke-width="5" fill="none" opacity=".8"/>
<path d="M610 600 L705 575 L690 602Z" fill="#0038e2" opacity=".85"/>
<rect x="335" y="190" width="155" height="555" rx="75" fill="#0b0c10" stroke="url(#mtr)" stroke-width="10"/>
<rect x="360" y="215" width="105" height="205" rx="26" fill="#2a2e3a" stroke="#4a5060" stroke-width="2"/><path d="M366 250 H460 M366 280 H460 M366 310 H460 M366 340 H460 M366 370 H460" stroke="#14161c" stroke-width="5"/>
<g fill="#fd9d02" opacity=".85"><circle cx="392" cy="447" r="9"/><circle cx="435" cy="447" r="9"/><circle cx="392" cy="490" r="9"/><circle cx="435" cy="490" r="9"/></g>
<rect x="362" y="525" width="102" height="80" rx="36" fill="#20232c" stroke="#4a5060" stroke-width="2"/><rect x="362" y="615" width="102" height="100" rx="36" fill="#20232c" stroke="#4a5060" stroke-width="2"/>
<rect x="54" y="445" width="38" height="190" rx="16" fill="#2a2e3a" stroke="#4a5060" stroke-width="2"/><rect x="60" y="640" width="42" height="215" rx="18" fill="#2a2e3a" stroke="#4a5060" stroke-width="2"/></svg>`;
}

export const ART = { displaypad, makalu67: makalu, everest_max: everestMax, everest60, macropad };
/** The drawings that carry a layer of buttons on a device screen. */
export const PAGE_ART = { macropad: macropadPage };
export const artFor = (id) => (ART[id] || displaypad)();
