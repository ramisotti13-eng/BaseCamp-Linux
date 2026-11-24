// displaypad: the Profile page (js/profiles-page.js).
import { frame } from "./dp-common.js";
import { mountProfiles } from "../profiles-page.js";

let scope = null;

export async function mount(el) {
  const fr = frame(el, "profile", true);
  scope = await mountProfiles(fr.col, fr.main, "displaypad");
}

export function unmount() { if (scope) scope.dispose(); scope = null; }
