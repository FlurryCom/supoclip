// Box math for the framing editor: {x, y, w, h} in fractions of the live frame, always inside it.
export type Box = { x: number; y: number; w: number; h: number };
export type Corner = "nw" | "ne" | "sw" | "se";

export const MIN_SIDE = 0.04; // the backend refuses smaller boxes
export const FULL_FRAME: Box = { x: 0, y: 0, w: 1, h: 1 };
export const CORNER_PRESETS: Record<Corner, Box> = {
  nw: { x: 0.012, y: 0.02, w: 0.28, h: 0.36 },
  ne: { x: 0.708, y: 0.02, w: 0.28, h: 0.36 },
  sw: { x: 0.012, y: 0.62, w: 0.28, h: 0.36 },
  se: { x: 0.708, y: 0.62, w: 0.28, h: 0.36 },
};

const clamp = (value: number, low: number, high: number) => Math.min(high, Math.max(low, value));

export function moveBox(start: Box, dx: number, dy: number): Box {
  return { ...start, x: clamp(start.x + dx, 0, 1 - start.w), y: clamp(start.y + dy, 0, 1 - start.h) };
}

export function resizeBox(start: Box, corner: Corner, dx: number, dy: number): Box {
  let [left, top, right, bottom] = [start.x, start.y, start.x + start.w, start.y + start.h];
  if (corner.endsWith("w")) left = clamp(left + dx, 0, right - MIN_SIDE);
  else right = clamp(right + dx, left + MIN_SIDE, 1);
  if (corner.startsWith("n")) top = clamp(top + dy, 0, bottom - MIN_SIDE);
  else bottom = clamp(bottom + dy, top + MIN_SIDE, 1);
  return { x: left, y: top, w: right - left, h: bottom - top };
}

export function drawBox(ax: number, ay: number, bx: number, by: number): Box {
  const side = (a: number, b: number) => {
    const size = Math.max(Math.abs(clamp(b, 0, 1) - clamp(a, 0, 1)), MIN_SIDE);
    return [clamp(Math.min(clamp(a, 0, 1), clamp(b, 0, 1)), 0, 1 - size), size];
  };
  const [x, w] = side(ax, bx);
  const [y, h] = side(ay, by);
  return { x, y, w, h };
}

// Size (in % of the area) of a region shown whole and centred, like the render's "fit" (no stretching).
export function fitInside(regionAspect: number, areaAspect: number) {
  return regionAspect > areaAspect
    ? { width: 100, height: Math.round((areaAspect / regionAspect) * 1000) / 10 }
    : { width: Math.round((regionAspect / areaAspect) * 1000) / 10, height: 100 };
}

// 4 decimals: enough for any resolution, and the saved JSON stays short.
export const rounded = (box: Box): Box => ({
  x: Math.round(box.x * 1e4) / 1e4,
  y: Math.round(box.y * 1e4) / 1e4,
  w: Math.round(box.w * 1e4) / 1e4,
  h: Math.round(box.h * 1e4) / 1e4,
});
