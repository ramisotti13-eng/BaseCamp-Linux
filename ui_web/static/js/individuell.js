// The Everest Max's Individuell brush as plain data, without the page (screens/em-rgb.js draws it;
// tools/test_individuell.py checks it with node).
//
// State E: {leds: [[r,g,b] x 126], side: [[r,g,b] x 45], bri, fx: [effect x 126], params: {effect:
// {effect (its colour mode), speed, color1, color2, direction}}, brush: effect id, colour: "#rrggbb"}.
// A key id is its LED index, a ring LED is RING + its index (as keyboard-flat.js numbers them).

export const RING = 200;

// The effects a key can run (the core's PER_KEY_EFFECTS) in the order of Base Camp's "Effekt
// auswählen", and their types (colour modes) in the order of its "Typ" list.
export const PK_FX = ["static", "wave", "tornado", "breathing", "reactive", "matrix", "yeti", "off"];
export const PK_VARIANTS = {
  static: ["static"], wave: ["wave", "wave-rainbow"], tornado: ["tornado", "tornado-rainbow"],
  breathing: ["breathing", "breathing-dual", "breathing-rainbow"], reactive: ["reactive"], matrix: ["matrix"],
  yeti: ["yeti"], off: [],
};
// Base Camp's defaults for an effect: speed 60, both colours #0044ff; wave, tornado and breathing
// start as rainbow (its Type 2), reactive, matrix and yeti with their two colours.
const DEFAULT_VARIANT = { wave: "wave-rainbow", tornado: "tornado-rainbow", breathing: "breathing-rainbow" };
export const fxDefaults = (id) => ({ effect: DEFAULT_VARIANT[id] || id, speed: 60, color1: "#0044ff", color2: "#0044ff", direction: "" });

/** The settings of an effect, made with the defaults the first time (shared by every key running it). */
export function fxSetting(E, id) { return (E.params[id] = E.params[id] || fxDefaults(id)); }

/** "single" | "dual" | "rainbow": what Typ calls a colour mode; `info` is the core's effect_info of it. */
export const typeOf = (variant, info) => (variant.endsWith("-rainbow") ? "rainbow" : info && info.color2 ? "dual" : "single");

const rgbOf = (s) => { const v = s.replace("#", ""); return [0, 2, 4].map((i) => parseInt(v.slice(i, i + 2), 16)); };
const same = (a, b) => a[0] === b[0] && a[1] === b[1] && a[2] === b[2];

/** Gives `ids` the brush; true when anything changed. The static brush sets a key's colour (and the
 *  ring's); an effect brush sets the key's effect and keeps its static colour; the ring LEDs are not
 *  in the keyboard's effect table, so an effect brush leaves them. */
export function paintKeys(E, ids) {
  const c = rgbOf(E.colour);
  let any = false;
  for (const id of ids) {
    if (id >= RING) {
      if (E.brush !== "static" || same(E.side[id - RING], c)) continue;
      E.side[id - RING] = c.slice();
    } else if (E.brush === "static") {
      if (E.fx[id] === "static" && same(E.leds[id], c)) continue;
      E.fx[id] = "static"; E.leds[id] = c.slice();
    } else {
      if (E.fx[id] === E.brush) continue;
      E.fx[id] = E.brush;
    }
    any = true;
  }
  return any;
}

/** Reset: every key and ring LED static white (Base Camp's default colour). */
export function resetAll(E) {
  E.leds = E.leds.map(() => [255, 255, 255]);
  E.side = E.side.map(() => [255, 255, 255]);
  E.fx = E.fx.map(() => "static");
}

/** True when a key runs an effect other than static: the keyboard runs those only from its saved
 *  custom slot, so every write must save. */
export function needsSave(E) {
  return E.fx.some((f) => f !== "static");
}

/** everest_max.apply_per_key's parameters for the state: the whole picture. `persist` saves it in the
 *  keyboard's custom slot (a second blink, a flash write); without it the keyboard only shows it. With
 *  a per-key effect in the picture it is always saved (an effect only runs from there). */
export function writeParams(E, persist = false) {
  const effect_params = {};
  for (const [id, p] of Object.entries(E.params)) {
    if (id === "static" || id === "off") continue;
    effect_params[id] = { effect: p.effect, speed: p.speed, brightness: E.bri, color1: p.color1, color2: p.color2 };
    if (p.direction) effect_params[id].direction = p.direction;
  }
  return { leds: E.leds, side: E.side, brightness: E.bri, effects: E.fx, effect_params, persist: persist || needsSave(E) };
}
