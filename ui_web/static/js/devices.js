// The device list: what exists, and which are present (devices.state / devices.changed).
import bc from "./bridge.js";

/** Every device the app supports, in the order of the home screen. Names are product names. */
export const DEVICES = [
  { id: "displaypad", name: "DisplayPad", type: "keypad", route: "#/displaypad/keys", ready: true },
  { id: "makalu67", name: "Makalu 67", type: "mouse", route: "#/makalu/buttons", ready: true },
  { id: "everest_max", name: "Everest Max", type: "keyboard", route: "#/everest_max/rgb", ready: true },
  { id: "everest60", name: "Everest 60", type: "keyboard", route: "#/everest60/rgb", ready: true },
  { id: "macropad", name: "MacroPad", type: "keypad", route: "#/macropad/keys", ready: true },
];

let state = { present: {}, denied: {}, keyboard_id: "everest_max" };
const subs = new Set();

function apply(p) {
  const s = p && (p.state && typeof p.state === "object" ? p.state : p);
  if (!s || typeof s !== "object") return;
  if (s.present && typeof s.present === "object") {
    state = { present: { ...s.present }, denied: s.denied || {}, keyboard_id: s.keyboard_id || state.keyboard_id };
    subs.forEach((fn) => fn(state));
  }
}

export async function init() {
  apply(await bc.call("devices.state"));
  bc.on("devices.changed", (_t, p) => { apply(p); refreshMakalu(); });
  bc.on("makalu.presence", () => refreshMakalu());
  // A pad made or deleted changes whether the DisplayPad's tab is in the top bar.
  bc.on("vpads.changed", (_t, p) => { if (!p || !p.what || p.what === "list") refreshVirtualPads(); });
  await Promise.all([refreshMakalu(), refreshVirtualPads()]);
}

// The virtual pads live under the DisplayPad: its tab shows while one exists, plugged in or not.
let virtualPads = 0;
async function refreshVirtualPads() {
  const r = await bc.call("vpads.list");
  const n = r && r.ok && Array.isArray(r.pads) ? r.pads.length : 0;
  if (n === virtualPads) return;
  virtualPads = n;
  subs.forEach((fn) => fn(state));
}

// The mouse slot is "makalu67" for both models; the model decides its name, its photos and its buttons.
let makaluModel = null;   // the makalu.detect / makalu.status reply: {model, is_max, buttons, ...}
export const makaluInfo = () => makaluModel;
const isMax = () => !!(makaluModel && makaluModel.is_max);
/** Photo key of a device: the Makalu Max has its own pictures. */
export const photoId = (id) => (id === "makalu67" && isMax() ? "makalu_max" : id);

export async function refreshMakalu() {
  if (!state.present.makalu67) return makaluModel;
  const r = await bc.call("makalu.detect");
  if (!r || !r.ok || !r.model) return makaluModel;
  const changed = !makaluModel || makaluModel.model !== r.model;
  makaluModel = r;
  const d = DEVICES.find((x) => x.id === "makalu67");
  d.name = r.model;
  if (changed) { subs.forEach((fn) => fn(state)); photoSubs.forEach((fn) => fn()); }
  return makaluModel;
}

// Optional device photos (device_images service). Off unless imported; the SVG drawings are the default.
let photos = {};            // id -> {home, page} absolute paths
let sources = {};           // id -> {home, page} source file names inside the Base Camp install
let hintHidden = true;      // the hint on the drawings (photo-hint.js) is hidden; known after the first status
export const photoSource = (id, kind) => { const k = photoId(id); return (sources[k] && sources[k][kind]) || null; };
const photoSubs = new Set();
const photoUrls = new Map();   // "path|size" -> data URL | null

async function loadPhotos() {
  const r = await bc.call("device_images.status");
  photos = r && r.ok && r.imported !== false && r.devices && typeof r.devices === "object" ? r.devices : {};
  sources = r && r.ok && r.sources && typeof r.sources === "object" ? r.sources : {};
  hintHidden = !(r && r.ok) || !!r.hint_hidden;
  photoUrls.clear();
  photoSubs.forEach((fn) => fn());
}
export async function initPhotos() {
  await loadPhotos();
  bc.on("device_images.changed", () => loadPhotos());
}
export function onPhotos(fn) { photoSubs.add(fn); return () => photoSubs.delete(fn); }
/** Whether a device has a photo of that kind ("home" or "page"). */
export const hasPhoto = (id, kind) => { const pid = photoId(id); return !!(photos[pid] && photos[pid][kind]); };
/** Whether the hint on the drawings was hidden by the person. */
export const isPhotoHintHidden = () => hintHidden;
/** Data URL of a device's photo ("home" or "page"), or null when there is none. */
export async function photoUrl(id, kind, size = 700) {
  const pid = photoId(id);
  const p = photos[pid] && photos[pid][kind];
  if (!p) return null;
  const k = p + "|" + size;
  if (!photoUrls.has(k)) photoUrls.set(k, await bc.image(p, size));
  return photoUrls.get(k);
}

export const isPresent = (id) => !!state.present[id];
/** Whether the top bar has a tab for the device: it is plugged in, or (the DisplayPad) a virtual pad exists. */
export const inTopBar = (id) => isPresent(id) || (id === "displaypad" && virtualPads > 0);
/** Where the device's tab leads: the DisplayPad without the pad goes to its virtual pads. */
export const tabRoute = (d) => (d.id === "displaypad" && !isPresent(d.id) && virtualPads > 0 ? "#/displaypad/pads" : d.route);
export const getState = () => state;
export function onChange(fn) { subs.add(fn); return () => subs.delete(fn); }
export const byId = (id) => DEVICES.find((d) => d.id === id);
