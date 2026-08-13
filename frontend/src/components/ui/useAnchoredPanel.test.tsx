import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import ColorPicker from "@/components/ColorPicker";

/**
 * The panels this hook positions live inside modals and scrolling rails, both
 * of which hide their own overflow. Positioned the ordinary way they render
 * into the clipped region and read as not having opened at all. So the two
 * things worth asserting are that the panel leaves that box -- fixed, not
 * absolute -- and that it flips above its trigger when below cannot hold it.
 *
 * jsdom lays nothing out, so the measurements the hook reads are stubbed.
 */

const ORIGINAL_HEIGHT = window.innerHeight;
const ORIGINAL_RECT = HTMLElement.prototype.getBoundingClientRect;

function stubLayout({ triggerTop, panelHeight }: { triggerTop: number; panelHeight: number }) {
  Object.defineProperty(window, "innerHeight", { configurable: true, value: 600 });
  Object.defineProperty(window, "innerWidth", { configurable: true, value: 360 });
  Object.defineProperty(HTMLElement.prototype, "scrollHeight", {
    configurable: true,
    value: panelHeight,
  });
  HTMLElement.prototype.getBoundingClientRect = function () {
    return {
      top: triggerTop,
      bottom: triggerTop + 40,
      left: 20,
      right: 120,
      width: 100,
      height: 40,
      x: 20,
      y: triggerTop,
      toJSON: () => ({}),
    } as DOMRect;
  };
}

const panelOf = (container: HTMLElement) =>
  container.querySelector<HTMLElement>('[style*="position: fixed"]');

afterEach(() => {
  HTMLElement.prototype.getBoundingClientRect = ORIGINAL_RECT;
  Object.defineProperty(window, "innerHeight", { configurable: true, value: ORIGINAL_HEIGHT });
  vi.restoreAllMocks();
});

describe("useAnchoredPanel", () => {
  it("takes the panel out of the scroll box that would clip it", () => {
    stubLayout({ triggerTop: 100, panelHeight: 200 });
    const { container } = render(<ColorPicker onColorSelect={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: /select color/i }));

    expect(panelOf(container)).not.toBeNull();
  });

  it("opens below the trigger when there is room", () => {
    stubLayout({ triggerTop: 100, panelHeight: 200 });
    const { container } = render(<ColorPicker onColorSelect={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: /select color/i }));

    // trigger bottom 140, plus the 8px gap.
    expect(panelOf(container)?.style.top).toBe("148px");
  });

  it("flips above the trigger when below cannot hold the panel", () => {
    stubLayout({ triggerTop: 500, panelHeight: 300 });
    const { container } = render(<ColorPicker onColorSelect={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: /select color/i }));

    // 44px below against 484 above, so above: 500 - 8 gap - 300 tall.
    expect(panelOf(container)?.style.top).toBe("192px");
  });

  it("caps the panel to the side it chose rather than letting it run off screen", () => {
    stubLayout({ triggerTop: 20, panelHeight: 900 });
    const { container } = render(<ColorPicker onColorSelect={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: /select color/i }));

    const panel = panelOf(container);
    // Neither side fits 900; below is the roomier, so it is capped and scrolls:
    // 600 viewport less the trigger's 60 bottom, the 8px gap and the 8px margin.
    expect(panel?.style.maxHeight).toBe("524px");
    expect(panel?.style.top).toBe("68px");
  });
});
