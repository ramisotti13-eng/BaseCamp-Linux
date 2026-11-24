// What the shell knows that more than one place shows: an update of the application and the
// number of plugins with a newer version. app.js fills it (from the core's events and from the
// state at start); the gear's badge and the Plugins tab read it.
// `focus`: the id of an element of the Settings screen to show (set by a screen, taken by app.js).
export const state = { update: null, plugins: 0, visible: true, focus: null };
const subs = new Set();

export function set(patch) {
  Object.assign(state, patch);
  subs.forEach((fn) => { try { fn(state); } catch (e) { console.error(e); } });
}

export function onChange(fn) { subs.add(fn); return () => subs.delete(fn); }
