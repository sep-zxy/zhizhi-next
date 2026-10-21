import { describe, expect, it } from "vitest";
import { calculateBottomOverlayOffset, type RectLike } from "../src/core/layout";

describe("bottom overlay offset", () => {
  const view: RectLike = { top: 0, right: 1440, bottom: 900, left: 1050, width: 390, height: 900 };

  it("reserves the overlapping status bar height", () => {
    const statusBar: RectLike = { top: 874, right: 1440, bottom: 900, left: 1110, width: 330, height: 26 };
    expect(calculateBottomOverlayOffset(view, statusBar)).toBe(26);
  });

  it("does not reserve space for a status bar outside this leaf", () => {
    const statusBar: RectLike = { top: 874, right: 900, bottom: 900, left: 600, width: 300, height: 26 };
    expect(calculateBottomOverlayOffset(view, statusBar)).toBe(0);
  });

  it("uses only the actual overlap when the bar extends below the view", () => {
    const statusBar: RectLike = { top: 890, right: 1440, bottom: 916, left: 1110, width: 330, height: 26 };
    expect(calculateBottomOverlayOffset(view, statusBar)).toBe(10);
  });
});
