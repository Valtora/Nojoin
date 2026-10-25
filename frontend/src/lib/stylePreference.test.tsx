import { act, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  APPEARANCE_STORAGE_KEYS,
  applyUiStyle,
  readStoredUiStyle,
  storeUiStyle,
} from "./appearance";
import { ThemeProvider } from "./ThemeProvider";

const root = document.documentElement;
const key = APPEARANCE_STORAGE_KEYS.style;

beforeEach(() => {
  vi.stubGlobal("matchMedia", vi.fn(() => ({
    matches: false,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  })));
});

afterEach(() => {
  vi.unstubAllGlobals();
  delete root.dataset.style;
  root.classList.remove("dark");
});

describe("style preference", () => {
  it.each(["classic", "depth"])("rejects stored %s on mount and cross-tab changes", (stored) => {
    localStorage.setItem(key, stored);
    render(<ThemeProvider><span>Content</span></ThemeProvider>);
    expect(root.dataset.style).toBe("default");

    for (const changedKey of [key, null]) {
      root.dataset.style = "stale";
      localStorage.setItem(key, stored);
      act(() => window.dispatchEvent(new StorageEvent("storage", { key: changedKey })));
      expect(root.dataset.style).toBe("default");
    }
  });

  it("validates storage before applying the root seam", () => {
    localStorage.setItem(key, "unrecognised");
    applyUiStyle(readStoredUiStyle());
    expect(root.dataset.style).toBe("default");

    storeUiStyle("default");
    expect(localStorage.getItem(key)).toBeNull();
  });

  it("survives blocked reads and writes", () => {
    vi.stubGlobal("localStorage", {
      getItem: () => { throw new Error("blocked"); },
      removeItem: () => { throw new Error("blocked"); },
    });

    expect(() => storeUiStyle("default")).not.toThrow();
    applyUiStyle(readStoredUiStyle());
    expect(root.dataset.style).toBe("default");
  });

  it("revalidates cross-tab changes and resets when storage is cleared", () => {
    const view = render(<ThemeProvider><span>Content</span></ThemeProvider>);
    expect(root.dataset.style).toBe("default");

    for (const changedKey of [key, null]) {
      root.dataset.style = "stale";
      localStorage.setItem(key, "unrecognised");
      act(() => window.dispatchEvent(new StorageEvent("storage", { key: changedKey })));
      expect(root.dataset.style).toBe("default");
    }

    view.unmount();
    root.dataset.style = "after-unmount";
    act(() => window.dispatchEvent(new StorageEvent("storage", { key })));
    expect(root.dataset.style).toBe("after-unmount");
  });
});
