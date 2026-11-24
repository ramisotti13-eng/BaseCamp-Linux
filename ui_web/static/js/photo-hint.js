// The hint on our own drawings of a device: Mountain's photos can be taken over from Base Camp
// (Settings -> Device photos & icons). A button opens that panel, the x hides the hint for good
// (device_images.set_hint_hidden); it is gone by itself once a photo of that device is there.
import bc from "./bridge.js";
import * as devices from "./devices.js";
import * as shell from "./shell-state.js";
import { t } from "./i18n.js";
import { h, icon } from "./ui.js";

/** The hint for device `id` and picture `kind` ("home" or "page"), or null when it is not wanted. */
export function photoHint(id, kind) {
  if (devices.hasPhoto(id, kind) || devices.isPhotoHintHidden()) return null;
  return h("div", { class: "photohint" },
    h("button", { type: "button", class: "photohint-go", title: t("photo_hint_title"),
      onclick: (e) => { e.preventDefault(); e.stopPropagation(); shell.set({ focus: "settings-photos" }); } },
      icon("photo"), h("span", null, t("photo_hint_btn"))),
    h("button", { type: "button", class: "photohint-x", title: t("photo_hint_hide"), "aria-label": t("photo_hint_hide"),
      onclick: (e) => { e.preventDefault(); e.stopPropagation(); bc.call("device_images.set_hint_hidden", { hidden: true }); } },
      icon("close")));
}
