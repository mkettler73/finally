import "@testing-library/jest-dom/vitest";
import { vi } from "vitest";

/**
 * jsdom has no layout, so every element measures 0x0 and a size-driven
 * component (the treemap) would render nothing. This double reports a fixed
 * viewport on observe, which is what a real browser does on first paint.
 */
export const TEST_BOX = { width: 400, height: 300 };

class MockResizeObserver {
  constructor(private readonly callback: ResizeObserverCallback) {}

  observe(target: Element) {
    this.callback(
      [{ target, contentRect: { ...TEST_BOX, x: 0, y: 0, top: 0, left: 0, right: TEST_BOX.width, bottom: TEST_BOX.height } } as unknown as ResizeObserverEntry],
      this as unknown as ResizeObserver,
    );
  }
  unobserve() {}
  disconnect() {}
}
globalThis.ResizeObserver = MockResizeObserver as never;

// jsdom has no canvas backend, so lightweight-charts cannot mount. The chart
// components are exercised through their data-shaping helpers instead.
vi.mock("lightweight-charts", () => ({
  createChart: () => ({
    addSeries: () => ({ setData: () => {}, update: () => {}, applyOptions: () => {} }),
    applyOptions: () => {},
    timeScale: () => ({ fitContent: () => {}, applyOptions: () => {} }),
    subscribeCrosshairMove: () => {},
    remove: () => {},
  }),
  AreaSeries: {},
  BaselineSeries: {},
  LineSeries: {},
  ColorType: { Solid: "solid" },
  CrosshairMode: { Normal: 0, Magnet: 1 },
  LineStyle: { Solid: 0, Dotted: 1, Dashed: 2 },
}));
