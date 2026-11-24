// Everest Max, Macro: the macro library of the application settings, inside this device's frame
// (Base Camp lists Macro in each device's rail).
import { mountIn, unmount } from "./set-macros.js";
import { frame } from "./em-common.js";

export const mount = (el) => mountIn(el, () => frame(el, "macro", true));
export { unmount };
