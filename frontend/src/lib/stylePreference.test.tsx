import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  APPEARANCE_STORAGE_KEYS,
  applyUiStyle,
  readStoredUiStyle,
  storeUiStyle,
  UI_STYLES,
} from "./appearance";
import { ThemeProvider, useTheme } from "./ThemeProvider";

const root = document.documentElement;
const key = APPEARANCE_STORAGE_KEYS.style;

function finishStyleLoad() {
  act(() => document.getElementById("nojoin-optional-style")!.dispatchEvent(new Event("load")));
}

function StyleChoices() {
  const { uiStyle, setUiStyle } = useTheme();
  return <>
    <span data-testid="choice">{uiStyle}</span>
    <button onClick={() => setUiStyle("classic")}>Classic</button>
    <button onClick={() => setUiStyle("depth")}>Depth</button>
  </>;
}

beforeEach(() => {
  root.dataset.style = "default";
  vi.stubGlobal("matchMedia", vi.fn(() => ({
    matches: false,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  })));
});

afterEach(() => {
  applyUiStyle("default");
  vi.unstubAllGlobals();
  delete root.dataset.style;
  root.classList.remove("dark");
});

describe("style preference", () => {
  it("offers exactly the three supported styles", () => {
    expect(UI_STYLES).toEqual(["default", "classic", "depth"]);
  });

  it.each(["default", "classic", "depth"])("applies stored %s on mount", (stored) => {
    localStorage.setItem(key, stored);
    render(<ThemeProvider><span>Content</span></ThemeProvider>);

    expect(readStoredUiStyle()).toBe(stored);
    expect(root.dataset.style).toBe("default");
    if (stored !== "default") finishStyleLoad();
    expect(root.dataset.style).toBe(stored);
  });

  it.each(["classic", "depth"])("follows %s changed in another tab", (stored) => {
    render(<ThemeProvider><span>Content</span></ThemeProvider>);
    localStorage.setItem(key, stored);

    act(() => window.dispatchEvent(new StorageEvent("storage", { key })));

    expect(root.dataset.style).toBe("default");
    expect(root.style.opacity).toBe("");
    finishStyleLoad();
    expect(root.dataset.style).toBe(stored);
  });

  it("keeps content visible while setter choices are pending and applies the last choice", () => {
    render(<ThemeProvider><StyleChoices /></ThemeProvider>);
    fireEvent.click(screen.getByRole("button", { name: "Classic" }));
    expect(screen.getByTestId("choice")).toHaveTextContent("classic");
    expect(localStorage.getItem(key)).toBe("classic");
    expect(root.dataset.style).toBe("default");
    expect(root.style.opacity).toBe("");

    fireEvent.click(screen.getByRole("button", { name: "Depth" }));
    expect(screen.getByTestId("choice")).toHaveTextContent("depth");
    expect(root.dataset.style).toBe("default");
    finishStyleLoad();
    expect(root.dataset.style).toBe("depth");
  });

  it("uses the last cross-tab choice while a sheet is pending", () => {
    render(<ThemeProvider><span>Content</span></ThemeProvider>);
    for (const stored of ["classic", "depth"]) {
      localStorage.setItem(key, stored);
      act(() => window.dispatchEvent(new StorageEvent("storage", { key })));
      expect(root.dataset.style).toBe("default");
    }
    finishStyleLoad();
    expect(root.dataset.style).toBe("depth");
  });

  it.each(["unrecognised", "Classic", '\"><script>'])(
    "rejects stored %s on mount and cross-tab changes",
    (stored) => {
      localStorage.setItem(key, stored);
      render(<ThemeProvider><span>Content</span></ThemeProvider>);
      expect(root.dataset.style).toBe("default");

      for (const changedKey of [key, null]) {
        root.dataset.style = "stale";
        localStorage.setItem(key, stored);
        act(() => window.dispatchEvent(new StorageEvent("storage", { key: changedKey })));
        expect(root.dataset.style).toBe("default");
      }
    },
  );

  it("validates storage before applying the root seam", () => {
    localStorage.setItem(key, "unrecognised");
    applyUiStyle(readStoredUiStyle());
    expect(root.dataset.style).toBe("default");

    storeUiStyle("default");
    expect(localStorage.getItem(key)).toBeNull();
    expect(root.dataset.style).toBe("default");
    expect(document.getElementById("nojoin-optional-style")).toBeNull();
  });

  it("survives blocked reads and writes", () => {
    vi.stubGlobal("localStorage", {
      getItem: () => { throw new Error("blocked"); },
      setItem: () => { throw new Error("blocked"); },
      removeItem: () => { throw new Error("blocked"); },
    });

    expect(() => storeUiStyle("depth")).not.toThrow();
    applyUiStyle("depth");
    expect(root.dataset.style).toBe("default");
    expect(document.getElementById("nojoin-optional-style")).not.toBeNull();
    finishStyleLoad();
    expect(root.dataset.style).toBe("depth");
    expect(() => storeUiStyle("default")).not.toThrow();
    applyUiStyle(readStoredUiStyle());
    expect(root.dataset.style).toBe("default");
  });

  it("returns to Default when another tab clears storage", () => {
    localStorage.setItem(key, "depth");
    render(<ThemeProvider><span>Content</span></ThemeProvider>);
    finishStyleLoad();
    expect(root.dataset.style).toBe("depth");
    expect(document.getElementById("nojoin-optional-style")).not.toBeNull();

    localStorage.clear();
    act(() => window.dispatchEvent(new StorageEvent("storage", { key: null })));

    expect(root.dataset.style).toBe("default");
    expect(document.getElementById("nojoin-optional-style")).toBeNull();
    expect(root.style.opacity).toBe("");
  });

  it("removes its cross-tab listener on unmount", () => {
    const view = render(<ThemeProvider><span>Content</span></ThemeProvider>);

    view.unmount();
    root.dataset.style = "after-unmount";
    act(() => window.dispatchEvent(new StorageEvent("storage", { key })));
    expect(root.dataset.style).toBe("after-unmount");
  });
});
