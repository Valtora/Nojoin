import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { fireEvent, renderWithProviders, screen } from "@/test/renderWithProviders";

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
  for (const attribute of ["data-palette", "data-corners", "data-ui-density"]) {
    root.removeAttribute(attribute);
  }
});

describe("AppearanceSettings", () => {
  it("shows the choices already stored for this browser", () => {
    localStorage.setItem("nojoin-palette", "ultraviolet");
    localStorage.setItem("nojoin-corners", "subtle");
    localStorage.setItem("nojoin-density", "comfortable");

    renderSettings();

    expect(screen.getByRole("combobox", { name: "Colour palette" })).toHaveValue("ultraviolet");
    expect(screen.getByRole("combobox", { name: "Corner style" })).toHaveValue("subtle");
    expect(screen.getByRole("combobox", { name: "Density" })).toHaveValue("comfortable");
  });

  it("falls back to the defaults when storage holds an unknown value", () => {
    localStorage.setItem("nojoin-palette", "neon");

    renderSettings();

    expect(screen.getByRole("combobox", { name: "Colour palette" })).toHaveValue("default");
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
