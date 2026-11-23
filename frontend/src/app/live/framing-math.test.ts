import { describe, expect, it } from "vitest";

import { CORNER_PRESETS, MIN_SIDE, drawBox, fitInside, moveBox, resizeBox } from "./framing-math";

describe("framing boxes stay inside the frame", () => {
  it("moves without leaving the frame", () => {
    expect(moveBox({ x: 0.8, y: 0.1, w: 0.3, h: 0.3 }, 0.5, 0)).toEqual({ x: 0.7, y: 0.1, w: 0.3, h: 0.3 });
  });

  it("resizes with the opposite corner anchored", () => {
    const box = resizeBox({ x: 0.2, y: 0.2, w: 0.3, h: 0.3 }, "nw", 0.5, 0);
    expect(box.x).toBeCloseTo(0.46);
    expect(box.w).toBeCloseTo(MIN_SIDE);
    expect(resizeBox({ x: 0.5, y: 0.5, w: 0.3, h: 0.3 }, "se", 1, 1)).toEqual({ x: 0.5, y: 0.5, w: 0.5, h: 0.5 });
  });

  it("draws from any two corners, never smaller than the minimum", () => {
    const box = drawBox(0.6, 0.6, 0.2, 0.1);
    expect(box.x).toBeCloseTo(0.2);
    expect(box.y).toBeCloseTo(0.1);
    expect(box.w).toBeCloseTo(0.4);
    expect(box.h).toBeCloseTo(0.5);
    const tiny = drawBox(0.99, 0.5, 0.99, 0.5);
    expect(tiny.w).toBe(MIN_SIDE);
    expect(tiny.x + tiny.w).toBeLessThanOrEqual(1);
  });

  it("corner presets fit in the frame", () => {
    for (const box of Object.values(CORNER_PRESETS)) {
      expect(box.x + box.w).toBeLessThanOrEqual(1);
      expect(box.y + box.h).toBeLessThanOrEqual(1);
    }
  });

  it("fits a region inside an area like the render (no stretching)", () => {
    expect(fitInside(3, 1.5)).toEqual({ width: 100, height: 50 }); // wider than the area
    expect(fitInside(0.75, 1.5)).toEqual({ width: 50, height: 100 }); // taller than the area
  });
});
