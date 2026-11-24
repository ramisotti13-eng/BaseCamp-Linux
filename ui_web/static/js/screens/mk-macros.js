// Makalu, Macro: the macro library of the application settings, inside the mouse's frame
// (Base Camp lists Macro in each device's rail).
import { mountIn, unmount } from "./set-macros.js";
import { frame, loadModel } from "./mk-common.js";

export const mount = async (el) => {
  const info = await loadModel();
  return mountIn(el, () => frame(el, "macro", true, info.model));
};
export { unmount };
