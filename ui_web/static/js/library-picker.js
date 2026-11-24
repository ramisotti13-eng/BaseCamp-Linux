// The DisplayPad library as a modal grid (displaypad.library): the library's own pictures and
// each picture pack's (a subfolder, such as the Mountain icon pack), filtered by pack.
import bc from "./bridge.js";
import { t } from "./i18n.js";
import { h, clear, modal, toast } from "./ui.js";

const ALL = "\u0000all";
const OWN = "\u0000own";

/** Promise of the picked picture's path, or null when cancelled. */
export async function pickFromLibrary() {
  const r = await bc.call("displaypad.library");
  if (!r || !r.ok) {
    toast(t("ui_error", { err: (r && r.error) || "displaypad.library" }), "err");
    return null;
  }
  const entries = (Array.isArray(r.entries) ? r.entries : []).filter((e) => e && typeof e.path === "string");
  const packs = Array.isArray(r.packs) ? r.packs : [...new Set(entries.map((e) => e.pack).filter(Boolean))];
  const options = [[ALL, t("dp_lib_all")]];
  if (entries.some((e) => !e.pack) && packs.length) options.push([OWN, t("dp_lib_own")]);
  for (const p of packs) options.push([p, p]);

  return new Promise((resolve) => {
    let filter = ALL;
    let closeDialog = null;
    const grid = h("div", { class: "libgrid" });
    const seg = h("div", { class: "seg libfilter", role: "radiogroup", "aria-label": t("dp_lib_filter") });

    // Thumbnails are asked for when their cell scrolls into view.
    const load = (cell) => {
      const img = cell.querySelector("img");
      if (!img || img.src) return;
      bc.image(cell.dataset.path, 96).then((url) => { if (url && img.isConnected) img.src = url; });
    };
    const io = "IntersectionObserver" in window
      ? new IntersectionObserver((seen) => {
        for (const s of seen) if (s.isIntersecting) { io.unobserve(s.target); load(s.target); }
      }, { root: grid, rootMargin: "160px" })
      : null;

    function renderFilters() {
      clear(seg);
      if (options.length < 2) { seg.hidden = true; return; }
      for (const [id, label] of options) {
        seg.append(h("button", { type: "button", role: "radio", class: "segbtn" + (id === filter ? " on" : ""),
          "aria-checked": id === filter ? "true" : "false",
          onclick: () => { filter = id; renderFilters(); renderGrid(); } }, label));
      }
    }

    function renderGrid() {
      if (io) io.disconnect();
      clear(grid);
      const list = entries.filter((e) => filter === ALL || (filter === OWN ? !e.pack : e.pack === filter));
      if (!list.length) { grid.append(h("div", { class: "empty" }, t("dp_lib_empty"))); return; }
      for (const e of list) {
        const label = e.pack ? `${e.name} (${e.pack})` : e.name;
        const cell = h("button", { type: "button", class: "libcell", title: label, "aria-label": label,
          "data-path": e.path, onclick: () => closeDialog && closeDialog(e.path) }, h("img", { alt: "" }));
        grid.append(cell);
        if (io) io.observe(cell); else load(cell);
      }
    }

    const { dlg, close } = modal({
      title: t("multi_upload_library"),
      body: h("div", null, seg, grid),
      buttons: (c) => [h("button", { class: "btn", type: "button", onclick: () => c(null) }, t("ui_cancel"))],
      onClose: (path) => { if (io) io.disconnect(); resolve(typeof path === "string" ? path : null); },
    });
    closeDialog = close;
    dlg.classList.add("wide");
    renderFilters();
    renderGrid();
    const first = dlg.querySelector(".segbtn.on") || dlg.querySelector(".libcell") || dlg.querySelector(".btn");
    if (first) first.focus();
  });
}
