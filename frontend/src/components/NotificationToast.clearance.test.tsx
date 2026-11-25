import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useNotificationStore } from "@/lib/notificationStore";

import BatchActionBar from "./BatchActionBar";
import NotificationToast from "./NotificationToast";

vi.mock("./BatchTagModal", () => ({ default: () => null }));

/**
 * Toasts rise above the recordings list's batch bar rather than cover it
 * (#974). jsdom lays nothing out, so the stack and the bar get the boxes they
 * would have on a 390 px phone, or on a desktop with the bar in the rail.
 */
type Box = { top: number; bottom: number; left: number; right: number };
let stackBox: Box;
let barBox: Box;

const rect = ({ top, bottom, left, right }: Box) =>
  ({
    top,
    bottom,
    left,
    right,
    width: right - left,
    height: bottom - top,
    x: left,
    y: top,
    toJSON: () => ({}),
  }) as DOMRect;

const setViewport = (width: number, height: number) => {
  Object.defineProperty(window, "innerWidth", { configurable: true, value: width });
  Object.defineProperty(window, "innerHeight", { configurable: true, value: height });
};

const stack = () => screen.getByText(/Worker Offline/).closest<HTMLElement>("[aria-live]")!;

const listWithSelection = (count: number) => (
  <>
    <BatchActionBar
      view="recordings"
      count={count}
      onClear={vi.fn()}
      onAction={vi.fn()}
      onTag={vi.fn()}
    />
    <NotificationToast />
  </>
);

describe("toasts and the batch bar", () => {
  const { innerWidth, innerHeight } = window;

  beforeEach(() => {
    useNotificationStore.setState({ activeNotifications: [], history: [] });
    useNotificationStore.getState().addNotification({
      type: "error",
      message: "Worker Offline: Background processing is paused.",
      persistent: true,
    });
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(
      function (this: HTMLElement) {
        if (this.matches("[aria-live].fixed")) return rect(stackBox);
        if (this.matches(".sticky.bottom-0")) return rect(barBox);
        return rect({ top: 0, bottom: 0, left: 0, right: 0 });
      },
    );
  });

  afterEach(() => {
    vi.restoreAllMocks();
    setViewport(innerWidth, innerHeight);
  });

  it("raises a toast above the batch bar on a phone, and lowers it when the bar goes", () => {
    setViewport(390, 844);
    stackBox = { top: 756, bottom: 828, left: 16, right: 374 };
    barBox = { top: 780, bottom: 844, left: 0, right: 390 };

    const { rerender } = render(listWithSelection(2));

    // Its bottom edge sits 8px above the bar's top: 844 - 780 + 8.
    expect(stack().style.bottom).toBe("72px");

    rerender(listWithSelection(0));
    expect(stack().style.bottom).toBe("16px");
  });

  it("follows the bar when it grows, as its buttons wrap onto a second line", () => {
    setViewport(390, 844);
    stackBox = { top: 756, bottom: 828, left: 16, right: 374 };
    barBox = { top: 780, bottom: 844, left: 0, right: 390 };
    render(listWithSelection(2));

    barBox = { top: 740, bottom: 844, left: 0, right: 390 };
    act(() => {
      window.dispatchEvent(new Event("resize"));
    });

    expect(stack().style.bottom).toBe("112px");
  });

  it("leaves a toast at the bottom right on a desktop, clear of the bar in the rail", () => {
    setViewport(1440, 900);
    stackBox = { top: 812, bottom: 884, left: 976, right: 1424 };
    barBox = { top: 836, bottom: 900, left: 64, right: 384 };

    render(listWithSelection(2));

    expect(stack().style.bottom).toBe("16px");
  });
});
