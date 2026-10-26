import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { applyUiStyle } from "./appearance";
import { themeScript } from "./theme-script";

const root = document.documentElement;
const id = "nojoin-optional-style";
const currentLink = () => document.getElementById(id) as HTMLLinkElement | null;

function prepaint(style: string | null) {
  if (style === null) localStorage.removeItem("nojoin-style");
  else localStorage.setItem("nojoin-style", style);
  new Function(themeScript)();
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.stubGlobal("matchMedia", vi.fn(() => ({ matches: false })));
  vi.spyOn(console, "warn").mockImplementation(() => {});
});

afterEach(() => {
  applyUiStyle("default");
  root.removeAttribute("style");
  delete root.dataset.style;
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

// First navigation still needs the original guard; hydrated switches do not.
describe("optional stylesheet via shipped prepaint script", () => {
  const apply = prepaint;
  it.each(["default", "invalid", "Classic", '\"><script>'])("creates no optional sheet for %s", (style) => {
    apply(style);

    expect(root.dataset.style).toBe("default");
    expect(currentLink()).toBeNull();
    expect(root.style.opacity).toBe("");
    expect([...document.styleSheets].some((sheet) => sheet.href?.endsWith("/styles/optional.css"))).toBe(false);
  });

  it("configures a single same-origin render-blocking link before insertion", () => {
    const append = vi.spyOn(document.head, "appendChild");
    append.mockImplementationOnce((node) => {
      const link = node as HTMLLinkElement;
      expect(link.getAttribute("blocking")).toBe("render");
      expect(link.getAttribute("href")).toBe("/styles/optional.css");
      expect(link.rel).toBe("stylesheet");
      expect(root.style.opacity).toBe("0");
      expect(root.style.getPropertyPriority("opacity")).toBe("important");
      return Node.prototype.appendChild.call(document.head, node);
    });

    apply("classic");
    const link = currentLink();
    apply("depth");
    apply("depth");

    expect(link).not.toBeNull();
    expect(currentLink()).toBe(link);
    expect(document.querySelectorAll(`#${id}`)).toHaveLength(1);
    expect(append).toHaveBeenCalledTimes(1);
    expect(root.dataset.style).toBe("depth");
    append.mockRestore();
  });

  it.each(["load", "error"])("restores previous inline opacity and priority on %s", (event) => {
    root.style.setProperty("opacity", "0.45", "important");
    apply("classic");

    currentLink()!.dispatchEvent(new Event(event));

    expect(root.style.getPropertyValue("opacity")).toBe("0.45");
    expect(root.style.getPropertyPriority("opacity")).toBe("important");
    vi.advanceTimersByTime(10000);
    expect(root.style.opacity).toBe("0.45");
  });

  it("restores an originally transparent page and bounds a stalled request", () => {
    root.style.opacity = "0";
    apply("depth");
    vi.advanceTimersByTime(10000);

    expect(root.style.opacity).toBe("0");
    expect(root.style.getPropertyPriority("opacity")).toBe("");
    expect(currentLink()).toBeNull();
  });

  it("releases an absent inline opacity on timeout and retries a failed link", () => {
    apply("classic");
    const failed = currentLink();
    vi.advanceTimersByTime(10000);
    expect(root.style.opacity).toBe("");
    expect(currentLink()).toBeNull();

    apply("depth");
    expect(currentLink()).not.toBe(failed);
    expect(root.style.opacity).toBe("0");
    currentLink()!.dispatchEvent(new Event("load"));
    expect(root.style.opacity).toBe("");
  });

  it("ignores callbacks from a timed-out render-blocking request", () => {
    apply("classic");
    const obsolete = currentLink()!;
    vi.advanceTimersByTime(10000);
    apply("depth");
    const latest = currentLink();

    obsolete.dispatchEvent(new Event("load"));
    obsolete.dispatchEvent(new Event("error"));

    expect(currentLink()).toBe(latest);
    expect(root.style.opacity).toBe("0");
    latest!.dispatchEvent(new Event("load"));
    expect(root.style.opacity).toBe("");
  });

  it("removes the active sheet and guard on Default, ignoring obsolete callbacks", () => {
    apply("classic");
    const obsolete = currentLink()!;
    apply("default");
    expect(currentLink()).toBeNull();
    expect(root.style.opacity).toBe("");

    apply("depth");
    const latest = currentLink();
    obsolete.dispatchEvent(new Event("load"));
    obsolete.dispatchEvent(new Event("error"));
    expect(currentLink()).toBe(latest);
    expect(root.style.opacity).toBe("0");
    expect(root.dataset.style).toBe("depth");

    latest!.dispatchEvent(new Event("load"));
    expect(root.style.opacity).toBe("");
    apply("default");
    expect(currentLink()).toBeNull();
  });

  it("guards the ancestor composite when a descendant explicitly opts into visibility", () => {
    const descendant = document.createElement("div");
    descendant.style.visibility = "visible";
    descendant.style.opacity = "1";
    document.body.appendChild(descendant);
    try {
      apply("classic");

      expect(root.style.opacity).toBe("0");
      expect(root.style.getPropertyPriority("opacity")).toBe("important");
      expect(descendant.style.visibility).toBe("visible");
      expect(descendant.style.opacity).toBe("1");

      apply("default");
      expect(root.style.opacity).toBe("");
    } finally {
      descendant.remove();
    }
  });
});

describe("prepaint/runtime cooperation", () => {
  it("reuses the prepaint request through hydration and style changes", () => {
    prepaint("classic");
    const link = currentLink();

    applyUiStyle("classic");
    applyUiStyle("depth");
    expect(currentLink()).toBe(link);
    link!.dispatchEvent(new Event("load"));
    expect(root.style.opacity).toBe("");
    expect(root.dataset.style).toBe("depth");
  });

  it.each(["load", "error", "timeout", "default"])("retains prepaint guard ownership through runtime reuse: %s", (event) => {
    root.style.setProperty("opacity", "0.45", "important");
    prepaint("classic");
    const original = currentLink()!;
    applyUiStyle("classic");
    applyUiStyle("depth");
    expect(currentLink()).toBe(original);
    expect(root.dataset.style).toBe("classic");

    if (event === "timeout") vi.advanceTimersByTime(10000);
    else if (event === "default") applyUiStyle("default");
    else original.dispatchEvent(new Event(event));

    expect(root.style.opacity).toBe("0.45");
    expect(root.style.getPropertyPriority("opacity")).toBe("important");
    expect(root.dataset.style).toBe(event === "load" ? "depth" : event === "default" ? "default" : "classic");
    if (event !== "load") {
      expect(currentLink()).toBeNull();
      applyUiStyle("depth");
      const latest = currentLink()!;
      original.dispatchEvent(new Event("load"));
      original.dispatchEvent(new Event("error"));
      expect(currentLink()).toBe(latest);
      expect(root.style.opacity).toBe("0.45");
      latest.dispatchEvent(new Event("load"));
      expect(root.dataset.style).toBe("depth");
    }
    expect(console.warn).toHaveBeenCalledTimes(event === "error" || event === "timeout" ? 1 : 0);
  });

  it.each(["missing", "blocked"])("does not request optional CSS with %s storage", (storage) => {
    if (storage === "blocked") {
      vi.stubGlobal("localStorage", { getItem() { throw new Error("blocked"); } });
      new Function(themeScript)();
    } else prepaint(null);

    expect(root.dataset.style).toBe("default");
    expect(currentLink()).toBeNull();
    expect(root.style.opacity).toBe("");
  });
});
