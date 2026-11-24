// The Everest Max key binding screen's pure parts (em-keys.js), testable without a page: the icon of each
// function, which key a pressed key is in the keyboard's numbering (the key field of a key combination),
// and how a binding is drawn.

/** The FUNKTION dropdown's icons (symbols in index.html). */
export const FN_ICON = {
  default: "keys", disable: "ban", os: "monitor", url: "globe", app: "play", folder: "folder", macro: "macro",
  media: "note", shortcut: "combo", hotkey: "fkey", mouse: "mouse", profile: "dd-profile", obs: "obs",
  plugin: "plug", own: "pencil",
};

// KeyboardEvent.code -> the key's SDK number (core/devices/everest_max/layout.py KEY_IDS). The key
// left of Enter on an ISO board reports "Backslash" too; there it is the key numbered 42.
const CODE_KID = {
  Escape: 110, PrintScreen: 124, ScrollLock: 125, Pause: 126, Insert: 75, Home: 80, PageUp: 85, Delete: 76,
  End: 81, PageDown: 86, ArrowLeft: 79, ArrowUp: 83, ArrowRight: 89, ArrowDown: 84, Backquote: 1, Minus: 12,
  Equal: 13, Backspace: 15, Tab: 16, BracketLeft: 27, BracketRight: 28, Backslash: 29, CapsLock: 30,
  Semicolon: 40, Quote: 41, Enter: 43, ShiftLeft: 44, IntlBackslash: 45, Comma: 53, Period: 54, Slash: 55,
  ShiftRight: 57, ControlLeft: 58, MetaLeft: 59, AltLeft: 60, Space: 61, AltRight: 62, ContextMenu: 63,
  ControlRight: 64, NumLock: 90, NumpadDivide: 95, NumpadMultiply: 100, NumpadSubtract: 105, NumpadAdd: 106,
  NumpadEnter: 108, NumpadDecimal: 104, Numpad0: 99, Numpad1: 93, Numpad2: 98, Numpad3: 103, Numpad4: 92,
  Numpad5: 97, Numpad6: 102, Numpad7: 91, Numpad8: 96, Numpad9: 101,
};
"QWERTYUIOP".split("").forEach((c, i) => { CODE_KID["Key" + c] = 17 + i; });
"ASDFGHJKL".split("").forEach((c, i) => { CODE_KID["Key" + c] = 31 + i; });
"ZXCVBNM".split("").forEach((c, i) => { CODE_KID["Key" + c] = 46 + i; });
"1234567890".split("").forEach((c, i) => { CODE_KID["Digit" + c] = 2 + i; });
for (let n = 1; n <= 12; n++) CODE_KID["F" + n] = 111 + n;
for (let n = 13; n <= 24; n++) CODE_KID["F" + n] = 217 + n;

const MODIFIER_CODES = new Set(["ShiftLeft", "ShiftRight", "ControlLeft", "ControlRight", "AltLeft", "AltRight", "MetaLeft", "MetaRight"]);

/** The key number for a pressed key, or null. `iso`: the board has the ISO shape. Modifiers alone are
 *  not a key for a combination (they are its checkboxes), except AltGr. */
export function kidOfCode(code, iso = false) {
  if (MODIFIER_CODES.has(code) && code !== "AltRight") return null;
  if (code === "Backslash" && iso) return 42;
  return CODE_KID[code] ?? null;
}

/** How the picture draws a binding: "off" for Deaktivieren, "bound" for any other, "" for Standard. */
export function stateOf(binding) {
  if (!binding || binding.function === "default") return "";
  return binding.function === "disable" ? "off" : "bound";
}

/** The BINDUNGEN list: the bound keys in the picture's order (`order`: kid -> position), and the
 *  selected key first when it is not bound. */
export function listKeys(keys, selected, order) {
  const pos = (k) => order.get(k) ?? 1e6 + k;
  const bound = Object.keys(keys).map(Number).filter((k) => stateOf(keys[k])).sort((a, b) => pos(a) - pos(b));
  return selected != null && !bound.includes(selected) ? [selected, ...bound] : bound;
}
