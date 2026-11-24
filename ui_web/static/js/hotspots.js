// Where the clickable keys sit on a device picture, in normalized coordinates [x, y, w, h] (0..1 of the picture box).
// The hotspots are a separate layer over the picture (an SVG drawing or an imported photo); they are never drawn into it.
//
// Tables are keyed by "device/view". Each has `drawing` (our own SVG, see PAD_DRAWING) and `photos`:
// {source file: rects}. A photo is used on a device page only when the core reports that very source file
// (device_images.status sources[device][view]) and a table for it exists here, so a click never lands beside a key.
// Later devices plug in the same way: Makalu (buttons 1-8 as numbered markers). The Everest keyboards are
// drawn from their layout data instead (keyboard-flat.js).

// DisplayPad drawing: 560x300 viewBox, 6x2 keys of 68x72 at (52 + 78 c, 64 + 84 r).
export const PAD_DRAWING = { w: 560, h: 300 };
const padDrawingRects = Array.from({ length: 12 }, (_, i) => [
  (52 + (i % 6) * 78) / 560, (64 + Math.floor(i / 6) * 84) / 300, 68 / 560, 72 / 300,
]);

// Measured on the Base Camp file DKD/DKD_Setting.png
const DKD_COLS = [0.1536, 0.2721, 0.3907, 0.5093, 0.6279, 0.7464];
const DKD_ROWS = [0.4473, 0.6176];
const dkdSetting = DKD_ROWS.flatMap((y) => DKD_COLS.map((x) => [x, y, 0.1, 0.142]));

// Makalu: numbered round markers, one per button in the order of the makalu.status buttons (ids "1".."8").
// The coordinates are pixel centres on the picture, turned into circles of diameter d: [x, y, w, h] normalized.
// The drawings (art.js makaluTop) are drawn in the pixel space of the photo, so the same centres serve both;
// each still has its own table entry. Measured on the imported pictures (grid and marker overlays on copies):
//   makalu67_page.png    364x800   source makalu67_light.png
//   makalu_max_page.png  778x1400  source Makalu/makalu_light.png
// Buttons 5 and 6 of the Makalu Max have no function of their own in the 3.x remap; they sit on the lower centre
// key and on the LED dots, a best guess.
export const MAKALU_PIC = { makalu67: { w: 364, h: 800 }, makalu_max: { w: 778, h: 1400 } };
const dots = (W, H, d, pts) => pts.map(([x, y]) => [(x - d / 2) / W, (y - d / 2) / H, d / W, d / H]);
const MK67_PTS = [[108, 300], [280, 300], [193, 292], [31, 472], [31, 380], [193, 450]];
const MKMAX_PTS = [[210, 330], [610, 330], [412, 305], [412, 565], [412, 665], [412, 468], [72, 535], [80, 750]];
// Measured on the Base Camp file MKD/MKD_setting.png (1400x968): the 12 key caps, M1..M6 above M7..M12.
const MKD_COLS = [218, 391, 565, 739, 912, 1086];
const MKD_ROWS = [440, 613];
const mkdSetting = MKD_ROWS.flatMap((y) => MKD_COLS.map((x) => [x / 1400, y / 968, 113 / 1400, 126 / 968]));

export const HOTSPOTS = {
  "displaypad/page": {
    drawing: padDrawingRects,
    photos: { "DKD/DKD_Setting.png": dkdSetting },
  },
  "makalu67/page": {
    drawing: dots(364, 800, 40, MK67_PTS),
    photos: {
      "makalu67_light.png": dots(364, 800, 40, MK67_PTS),
      "makalu67.png": dots(364, 809, 40, MK67_PTS.map(([x, y]) => [x, y + 9])),   // uncropped: 9 rows lower
    },
  },
  "makalu_max/page": {
    drawing: dots(778, 1400, 84, MKMAX_PTS),
    photos: {
      "Makalu/makalu_light.png": dots(778, 1400, 84, MKMAX_PTS),
      "Makalu/mouse.png": dots(778, 1400, 84, MKMAX_PTS),   // same size as the light photo, nothing cropped
    },
  },
  // The MacroPad drawing (art.js PAGE_ART.macropad) has the DisplayPad's key grid.
  "macropad/page": {
    drawing: padDrawingRects,
    photos: { "MKD/MKD_setting.png": mkdSetting },
  },
};

/** The key rectangles for a device view: the photo's table when `source` is known here, else null. */
export function photoRects(device, view, source) {
  const t = HOTSPOTS[device + "/" + view];
  return (t && source && t.photos[source]) || null;
}
export function drawingRects(device, view) {
  const t = HOTSPOTS[device + "/" + view];
  return t ? t.drawing : null;
}
export function hasPhotoTable(device, view, source) { return !!photoRects(device, view, source); }

// The light ring of the Makalu pictures (js/mouse-light.js), measured like the tables above. Per "device/view":
// `drawing` and `photos` {source file: ring}, each ring {mode, rect, stroke, pic}:
//   mode "behind": the photo is see-through at the ring (alpha < 128), the light sits behind the photo, clipped to rect
//   mode "over":   no see-through ring; the light is a ring-shaped stroke over the picture, `stroke` wide (of the box width)
//   rect [x, y, w, h] normalized to the picture, pic [w, h] the picture's pixels (its aspect ratio).
// Measured on Base Camp's files (alpha < 128 and not connected to the outside; the DPI buttons inside stay opaque):
//   makalu67_light.png 364x809, ring x 152-234, y 232-504; the import crops its 9 empty top rows (364x800: y 223-495)
//   makalu67.png       364x809, opaque; the ring at the same place
//   Makalu/makalu_light.png 1261x2268, ring x 540-802, y 290-1210, imported at 778x1400 (scaled, nothing cropped)
//   Makalu/mouse.png   1261x2268, opaque
const ringPx = (W, H, x0, y0, x1, y1) => [x0 / W, y0 / H, (x1 - x0 + 1) / W, (y1 - y0 + 1) / H];
const MKMAX_RING = ringPx(1261, 2268, 540, 290, 802, 1210);
export const RINGS = {
  "makalu67/page": {
    drawing: { mode: "over", rect: [152 / 364, 222 / 800, 80 / 364, 275 / 800], stroke: 8 / 364, pic: [364, 800] },
    photos: {
      "makalu67_light.png": { mode: "behind", rect: ringPx(364, 800, 152, 223, 234, 495), pic: [364, 800] },
      "makalu67.png": { mode: "over", rect: ringPx(364, 809, 152, 232, 234, 504), stroke: 9 / 364, pic: [364, 809] },
    },
  },
  "makalu_max/page": {
    drawing: { mode: "over", rect: [335 / 778, 190 / 1400, 155 / 778, 555 / 1400], stroke: 12 / 778, pic: [778, 1400] },
    photos: {
      "Makalu/makalu_light.png": { mode: "behind", rect: MKMAX_RING, pic: [778, 1400] },
      "Makalu/mouse.png": { mode: "over", rect: MKMAX_RING, stroke: 16 / 778, pic: [778, 1400] },
    },
  },
};
/** The ring of a device picture: the table of `source` (an imported Base Camp file), or the drawing's when source is
 *  null. Returns {mode, rect, stroke?, pic, aspect} (aspect = ring box width / height in picture pixels) or null. */
export function lightRing(device, view, source) {
  const t = RINGS[device + "/" + view];
  const r = t && (source ? t.photos[source] : t.drawing);
  return r ? { ...r, aspect: (r.rect[2] * r.pic[0]) / (r.rect[3] * r.pic[1]) } : null;
}
