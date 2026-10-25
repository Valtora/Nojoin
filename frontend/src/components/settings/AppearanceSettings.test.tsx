import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { act, fireEvent, renderWithProviders, screen, within } from "@/test/renderWithProviders";

import AppearanceSettings from "./AppearanceSettings";

const root = document.documentElement;

function stubViewport(width: number, height: number) {
  vi.stubGlobal("innerWidth", width);
  vi.stubGlobal("innerHeight", height);
}

function renderSettings() {
  return renderWithProviders(<AppearanceSettings />, { withTheme: true });
}

function choose(label: string, value: string) {
  fireEvent.change(screen.getByRole("combobox", { name: label }), {
    target: { value },
  });
}

beforeEach(() => {
  vi.stubGlobal(
    "matchMedia",
    vi.fn().mockImplementation((query: string) => ({
      matches: false,
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })),
  );
  stubViewport(1440, 900);
});

afterEach(() => {
  vi.unstubAllGlobals();
  root.className = "";
  for (const attribute of ["data-palette", "data-corners", "data-ui-density", "data-style"]) {
    root.removeAttribute(attribute);
  }
});

describe("AppearanceSettings", () => {
  it("shows the choices already stored for this browser", () => {
    localStorage.setItem("nojoin-palette", "ultraviolet");
    localStorage.setItem("nojoin-corners", "subtle");
    localStorage.setItem("nojoin-density", "comfortable");
    localStorage.setItem("nojoin-style", "depth");

    renderSettings();

    expect(screen.getByRole("combobox", { name: "Colour palette" })).toHaveValue("ultraviolet");
    expect(screen.getByRole("combobox", { name: "Corner style" })).toHaveValue("subtle");
    expect(screen.getByRole("combobox", { name: "Density" })).toHaveValue("comfortable");
    expect(screen.getByRole("combobox", { name: "Style" })).toHaveValue("depth");
  });

  it("falls back to the defaults when storage holds an unknown value", () => {
    localStorage.setItem("nojoin-palette", "neon");
    localStorage.setItem("nojoin-style", "neon");

    renderSettings();

    expect(screen.getByRole("combobox", { name: "Colour palette" })).toHaveValue("default");
    expect(screen.getByRole("combobox", { name: "Style" })).toHaveValue("default");
    expect(root.dataset.style).toBe("default");
  });

  it("treats an unknown stored theme as System", () => {
    localStorage.setItem("nojoin-theme", "purple");

    renderSettings();

    expect(screen.getByRole("combobox", { name: "Theme" })).toHaveValue("system");
    expect(root.classList.contains("dark")).toBe(false);
  });

  it("keeps working when the browser blocks storage", () => {
    const blocked = () => {
      throw new Error("storage blocked");
    };
    vi.stubGlobal("localStorage", {
      getItem: blocked,
      setItem: blocked,
      removeItem: blocked,
    });

    renderSettings();
    choose("Theme", "dark");
    choose("Colour palette", "graphite");
    choose("Style", "classic");

    expect(root.classList.contains("dark")).toBe(true);
    expect(root.dataset.palette).toBe("graphite");
    expect(root.dataset.style).toBe("classic");
    expect(screen.getByRole("combobox", { name: "Style" })).toHaveValue("classic");
  });

  it("names and describes the Style control and gives search its row anchor", () => {
    renderSettings();

    const select = screen.getByRole("combobox", { name: "Style" });
    expect(within(select).getAllByRole("option").map((option) => option.textContent))
      .toEqual(["Default", "Classic", "Depth"]);
    expect(select).toHaveAccessibleDescription(
      "The shape and depth of controls and panels. Classic looks best with Square corners.",
    );
    expect(document.getElementById("appearance-style")).toContainElement(select);
    expect(screen.getByTitle("Saved in this browser only, not with your account")).toBeInTheDocument();
  });

  it.each(["classic", "depth"])("applies and persists %s, then forgets Default", (style) => {
    renderSettings();

    choose("Style", style);
    expect(root.dataset.style).toBe(style);
    expect(localStorage.getItem("nojoin-style")).toBe(style);
    expect(screen.getByRole("combobox", { name: "Style" })).toHaveValue(style);

    choose("Style", "default");
    expect(root.dataset.style).toBe("default");
    expect(localStorage.getItem("nojoin-style")).toBeNull();
  });

  it("leaves corners, palette and density unchanged when Classic is chosen", () => {
    renderSettings();
    choose("Colour palette", "ultraviolet");
    choose("Corner style", "subtle");
    choose("Density", "comfortable");

    choose("Style", "classic");

    expect(root.dataset.palette).toBe("ultraviolet");
    expect(root.dataset.corners).toBe("subtle");
    expect(root.dataset.uiDensity).toBe("comfortable");
    for (const [label, key, value] of [
      ["Colour palette", "nojoin-palette", "ultraviolet"],
      ["Corner style", "nojoin-corners", "subtle"],
      ["Density", "nojoin-density", "comfortable"],
    ]) {
      expect(screen.getByRole("combobox", { name: label })).toHaveValue(value);
      expect(localStorage.getItem(key)).toBe(value);
    }
  });

  it("stores an explicit theme and forgets System, the default", () => {
    renderSettings();

    choose("Theme", "light");
    expect(localStorage.getItem("nojoin-theme")).toBe("light");

    choose("Theme", "system");
    expect(localStorage.getItem("nojoin-theme")).toBeNull();
  });

  it("applies and persists a palette, and returning to the default leaves no trace", () => {
    renderSettings();

    choose("Colour palette", "graphite");
    expect(root.dataset.palette).toBe("graphite");
    expect(localStorage.getItem("nojoin-palette")).toBe("graphite");

    choose("Colour palette", "default");
    expect(root.hasAttribute("data-palette")).toBe(false);
    expect(localStorage.getItem("nojoin-palette")).toBeNull();
  });

  it("applies and persists a corner style", () => {
    renderSettings();

    choose("Corner style", "square");
    expect(root.dataset.corners).toBe("square");
    expect(localStorage.getItem("nojoin-corners")).toBe("square");

    choose("Corner style", "rounded");
    expect(root.hasAttribute("data-corners")).toBe(false);
    expect(localStorage.getItem("nojoin-corners")).toBeNull();
  });

  it("overrides the automatic density and hands back to it", () => {
    renderSettings();
    // 1440x900 is a compact desktop under the automatic heuristic.
    expect(root.dataset.uiDensity).toBe("compact");

    choose("Density", "comfortable");
    expect(root.dataset.uiDensity).toBe("comfortable");
    expect(localStorage.getItem("nojoin-density")).toBe("comfortable");

    choose("Density", "auto");
    expect(root.dataset.uiDensity).toBe("compact");
    expect(localStorage.getItem("nojoin-density")).toBeNull();
  });

  it("forces compact on a phone-sized viewport when asked", () => {
    stubViewport(390, 844);
    renderSettings();
    expect(root.dataset.uiDensity).toBe("comfortable");

    choose("Density", "compact");

    expect(root.dataset.uiDensity).toBe("compact");
  });

  it("applies and persists the opt-in dense level", () => {
    renderSettings();

    choose("Density", "dense");

    expect(root.dataset.uiDensity).toBe("dense");
    expect(localStorage.getItem("nojoin-density")).toBe("dense");
  });
});

describe("AppearanceSettings across tabs", () => {
  // Another tab writes storage; this tab only hears the storage event.
  function changeInAnotherTab(key: string, value: string | null) {
    if (value === null) {
      localStorage.removeItem(key);
    } else {
      localStorage.setItem(key, value);
    }
    act(() => {
      window.dispatchEvent(new StorageEvent("storage", { key }));
    });
  }

  it("follows theme, palette, corners, density and style changed in another tab", () => {
    renderSettings();

    changeInAnotherTab("nojoin-theme", "dark");
    changeInAnotherTab("nojoin-palette", "classic");
    changeInAnotherTab("nojoin-corners", "square");
    changeInAnotherTab("nojoin-density", "dense");
    changeInAnotherTab("nojoin-style", "depth");

    expect(root.classList.contains("dark")).toBe(true);
    expect(root.dataset.palette).toBe("classic");
    expect(root.dataset.corners).toBe("square");
    expect(root.dataset.uiDensity).toBe("dense");
    expect(root.dataset.style).toBe("depth");
    expect(screen.getByRole("combobox", { name: "Theme" })).toHaveValue("dark");
    expect(screen.getByRole("combobox", { name: "Colour palette" })).toHaveValue("classic");
    expect(screen.getByRole("combobox", { name: "Corner style" })).toHaveValue("square");
    expect(screen.getByRole("combobox", { name: "Density" })).toHaveValue("dense");
    expect(screen.getByRole("combobox", { name: "Style" })).toHaveValue("depth");
  });

  it("returns to the defaults when another tab clears a setting", () => {
    renderSettings();
    changeInAnotherTab("nojoin-palette", "graphite");
    expect(root.dataset.palette).toBe("graphite");

    changeInAnotherTab("nojoin-palette", null);

    expect(root.hasAttribute("data-palette")).toBe(false);
    expect(screen.getByRole("combobox", { name: "Colour palette" })).toHaveValue("default");
  });

  it("returns the Style control and root to Default after removal or invalid storage", () => {
    renderSettings();

    for (const value of [null, "unrecognised"]) {
      changeInAnotherTab("nojoin-style", "classic");
      expect(screen.getByRole("combobox", { name: "Style" })).toHaveValue("classic");

      changeInAnotherTab("nojoin-style", value);

      expect(root.dataset.style).toBe("default");
      expect(screen.getByRole("combobox", { name: "Style" })).toHaveValue("default");
    }
  });

  it("updates the Style control and root when another tab clears all storage", () => {
    renderSettings();
    changeInAnotherTab("nojoin-style", "depth");
    expect(screen.getByRole("combobox", { name: "Style" })).toHaveValue("depth");

    localStorage.clear();
    act(() => window.dispatchEvent(new StorageEvent("storage", { key: null })));

    expect(root.dataset.style).toBe("default");
    expect(screen.getByRole("combobox", { name: "Style" })).toHaveValue("default");
  });
});
