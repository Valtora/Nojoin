import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { applyUiStyle } from "./appearance";

const root = document.documentElement;
const currentLink = () => document.getElementById("nojoin-optional-style") as HTMLLinkElement | null;

beforeEach(() => {
  vi.useFakeTimers();
  root.dataset.style = "default";
  vi.spyOn(console, "warn").mockImplementation(() => {});
});

afterEach(() => {
  applyUiStyle("default");
  root.removeAttribute("style");
  delete root.dataset.style;
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("runtime style loading", () => {
  it.each(["", "important"])("keeps the current appearance visible until load (%s opacity priority)", (priority) => {
    root.style.setProperty("opacity", "0.45", priority);
    const setOpacity = vi.spyOn(root.style, "setProperty");
    const removeOpacity = vi.spyOn(root.style, "removeProperty");
    const append = vi.spyOn(document.head, "appendChild");
    append.mockImplementationOnce((node) => {
      const link = node as HTMLLinkElement;
      expect(link.hasAttribute("blocking")).toBe(false);
      expect(link.rel).toBe("stylesheet");
      expect(link.getAttribute("href")).toBe("/styles/optional.css");
      expect(root.dataset.style).toBe("default");
      expect(root.style.opacity).toBe("0.45");
      return Node.prototype.appendChild.call(document.head, node);
    });

    applyUiStyle("classic");
    const link = currentLink()!;
    vi.advanceTimersByTime(9999);

    expect(link.hasAttribute("blocking")).toBe(false);
    expect(root.dataset.style).toBe("default");
    expect(root.style.opacity).toBe("0.45");
    expect(root.style.getPropertyPriority("opacity")).toBe(priority);
    expect(setOpacity).not.toHaveBeenCalled();
    expect(removeOpacity).not.toHaveBeenCalled();

    link.dispatchEvent(new Event("load"));
    expect(root.dataset.style).toBe("classic");
    expect(currentLink()).toBe(link);
    expect(root.style.opacity).toBe("0.45");
    expect(setOpacity).not.toHaveBeenCalled();
    expect(removeOpacity).not.toHaveBeenCalled();
    expect(vi.getTimerCount()).toBe(0);
  });

  it.each(["error", "timeout"])("keeps the previous style on %s, reports it and allows retry", (failure) => {
    applyUiStyle("classic");
    const failed = currentLink()!;
    if (failure === "error") failed.dispatchEvent(new Event("error"));
    else vi.advanceTimersByTime(10000);

    expect(root.dataset.style).toBe("default");
    expect(root.style.opacity).toBe("");
    expect(currentLink()).toBeNull();
    expect(console.warn).toHaveBeenCalledTimes(1);
    expect(console.warn).toHaveBeenCalledWith(expect.stringContaining("optional UI stylesheet"));
    expect(vi.getTimerCount()).toBe(0);

    applyUiStyle("depth");
    const retry = currentLink()!;
    failed.dispatchEvent(new Event("load"));
    failed.dispatchEvent(new Event("error"));
    expect(root.dataset.style).toBe("default");
    expect(currentLink()).toBe(retry);
    retry.dispatchEvent(new Event("load"));
    expect(root.dataset.style).toBe("depth");
    vi.advanceTimersByTime(10000);
    expect(currentLink()).toBe(retry);
    expect(console.warn).toHaveBeenCalledTimes(1);
  });

  it("applies only the last pending choice when the shared sheet loads", () => {
    applyUiStyle("classic");
    const link = currentLink()!;
    applyUiStyle("depth");

    expect(currentLink()).toBe(link);
    expect(root.dataset.style).toBe("default");
    expect(root.style.opacity).toBe("");
    link.dispatchEvent(new Event("load"));
    expect(root.dataset.style).toBe("depth");
    vi.advanceTimersByTime(10000);
    expect(root.dataset.style).toBe("depth");
    expect(currentLink()).toBe(link);
  });

  it("returns to Default immediately and ignores cancelled callbacks after another switch", () => {
    applyUiStyle("classic");
    const cancelled = currentLink()!;
    applyUiStyle("default");
    expect(root.dataset.style).toBe("default");
    expect(currentLink()).toBeNull();
    expect(vi.getTimerCount()).toBe(0);

    applyUiStyle("depth");
    const latest = currentLink()!;
    cancelled.dispatchEvent(new Event("load"));
    cancelled.dispatchEvent(new Event("error"));
    expect(currentLink()).toBe(latest);
    expect(root.dataset.style).toBe("default");
    latest.dispatchEvent(new Event("load"));
    expect(root.dataset.style).toBe("depth");
    vi.advanceTimersByTime(10000);
    expect(currentLink()).toBe(latest);
    expect(console.warn).not.toHaveBeenCalled();
  });

  it("switches Classic and Depth immediately once loaded, then removes the sheet on Default", () => {
    applyUiStyle("classic");
    const link = currentLink()!;
    link.dispatchEvent(new Event("load"));
    for (const style of ["depth", "classic", "depth"] as const) {
      applyUiStyle(style);
      expect(root.dataset.style).toBe(style);
      expect(currentLink()).toBe(link);
      expect(vi.getTimerCount()).toBe(0);
    }
    applyUiStyle("default");
    expect(root.dataset.style).toBe("default");
    expect(currentLink()).toBeNull();
  });
});
