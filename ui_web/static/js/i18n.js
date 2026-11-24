// t(key, params): strings come from the core (i18n.strings); the English text
// bundled in strings.js is the fallback, so the page reads right without a core.
import bc from "./bridge.js";
import { STRINGS } from "./strings.js";

let dict = {};
let lang = "en";

function fmt(s, params) {
  if (!params) return s;
  return s.replace(/\{(\w+)\}/g, (m, k) => (k in params ? String(params[k]) : m));
}

export function t(key, params) {
  let s = dict[key];
  if (typeof s !== "string") {
    const e = STRINGS[key];
    s = e ? e[0] : key;
  }
  return fmt(s, params);
}

export function currentLang() { return lang; }

/** Load the current language's dictionary. Tolerant: {strings:{..}}, {lang, strings}, or the dict itself. */
export async function load() {
  const r = await bc.call("i18n.strings");
  if (r && r.ok) {
    const d = r.strings && typeof r.strings === "object" ? r.strings : r.result && typeof r.result === "object" ? r.result : r;
    const next = {};
    for (const [k, v] of Object.entries(d)) if (typeof v === "string" && k !== "ok") next[k] = v;
    dict = next;
    lang = r.lang || r.language || r.code || lang;
  }
  document.documentElement.lang = lang;
}

/** {code: name} and the current code, from i18n.languages (tolerant). */
export async function languages() {
  const r = await bc.call("i18n.languages");
  if (!r || !r.ok) return { list: {}, current: lang };
  let list = r.languages || r.result || {};
  if (Array.isArray(list)) {
    const o = {};
    for (const x of list) { if (typeof x === "string") o[x] = x; else if (x && x.code) o[x.code] = x.name || x.code; }
    list = o;
  }
  return { list, current: r.current || r.lang || r.language || lang };
}
