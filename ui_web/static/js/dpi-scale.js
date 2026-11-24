// The Makalu's DPI scale like Base Camp's: one horizontal scale from the model's lowest to its highest DPI with ticks,
// and a round numbered marker per level that is dragged along it (or moved with the arrow keys). The scale is not
// linear: the useful levels sit low (400 to 3200) and on a straight scale their markers would lie on top of each
// other, so the square root of the DPI is laid out evenly.
import { t } from "./i18n.js";
import { h } from "./ui.js";

const TICKS = [50, 1000, 2000, 4000, 8000, 12000, 16000, 19000];
const tickLabel = (v) => (v >= 1000 ? v / 1000 + "K" : String(v));

/** `opts`: {min, max, step, levels, active, onInput(i, value), onActive(i)}. Returns {el, set(levels, active)}. */
export function dpiScale({ min, max, step, levels, active, onInput, onActive }) {
  const lo = Math.sqrt(min), span = Math.sqrt(max) - lo;
  const posOf = (v) => (Math.sqrt(Math.max(min, Math.min(max, v))) - lo) / span;
  const valueAt = (p) => {
    const root = lo + Math.max(0, Math.min(1, p)) * span;
    return Math.max(min, Math.min(max, Math.round((root * root) / step) * step));
  };
  const L = levels.slice();
  let act = active;

  const track = h("div", { class: "dscale-track" });
  const ticks = TICKS.filter((v) => v >= min && v <= max).map((v) =>
    h("span", { class: "dscale-tick", style: `left:${posOf(v) * 100}%` }, h("i"), h("b", null, tickLabel(v))));
  const markers = L.map((v, i) => {
    const m = h("button", { type: "button", class: "dscale-mark", role: "slider", "data-level": String(i), "aria-orientation": "horizontal",
      "aria-valuemin": String(min), "aria-valuemax": String(max) }, String(i + 1));
    let dragging = false;
    const move = (e) => {
      const r = track.getBoundingClientRect();
      L[i] = valueAt((e.clientX - r.left) / r.width);
      paint();
      onInput(i, L[i]);
    };
    m.addEventListener("pointerdown", (e) => {
      dragging = true;
      m.setPointerCapture(e.pointerId);
      if (act !== i) { act = i; paint(); onActive(i); }
      e.preventDefault();
    });
    m.addEventListener("pointermove", (e) => { if (dragging) move(e); });
    const end = () => { dragging = false; };
    m.addEventListener("pointerup", end);
    m.addEventListener("pointercancel", end);
    m.addEventListener("keydown", (e) => {
      const d = { ArrowRight: 1, ArrowUp: 1, ArrowLeft: -1, ArrowDown: -1 }[e.key];
      if (!d) return;
      e.preventDefault();
      L[i] = Math.max(min, Math.min(max, L[i] + d * step * (e.shiftKey ? 10 : 1)));
      paint();
      onInput(i, L[i]);
    });
    return m;
  });

  function paint() {
    markers.forEach((m, i) => {
      m.style.left = posOf(L[i]) * 100 + "%";
      m.style.zIndex = i === act ? "2" : "1";
      m.classList.toggle("act", i === act);
      m.setAttribute("aria-valuenow", String(L[i]));
      m.setAttribute("aria-valuetext", t("mk_set_marker", { n: i + 1, v: L[i] }));
      m.setAttribute("aria-label", t("mk_set_level", { n: i + 1 }));
    });
  }
  track.append(...ticks, ...markers);
  paint();
  return {
    el: h("div", { class: "dscale", role: "group", "aria-label": t("mk_set_scale") }, track),
    set(levels2, active2) { levels2.forEach((v, i) => { L[i] = v; }); act = active2; paint(); },
  };
}
