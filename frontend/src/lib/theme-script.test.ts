import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DEFAULT_PALETTE, PALETTES } from "./appearance";
import { themeScript } from "./theme-script";
import { resolveDensity } from "./viewportDensity";

/**
 * The script is shipped to the browser as a string and never compiled, so the
 * only meaningful test is to run that exact string. A TypeScript annotation
 * inside it once made it a syntax error that disabled it on every page load,
 * and nothing failed: React re-applied the theme after hydration and the only
 * symptom was a flash of the light theme.
 */
function runScript() {
  new Function(themeScript)();
}

function stubPrefersDark(matches: boolean) {
  vi.stubGlobal(
    "matchMedia",
    vi.fn().mockImplementation((query: string) => ({
      matches: query === "(prefers-color-scheme: dark)" ? matches : false,
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })),
  );
}

function setViewport(width: number, height: number) {
  vi.stubGlobal("innerWidth", width);
  vi.stubGlobal("innerHeight", height);
}

const root = document.documentElement;

function resetRoot() {
  root.className = "";
  for (const attribute of ["data-palette", "data-corners", "data-ui-density"]) {
    root.removeAttribute(attribute);
  }
}

beforeEach(resetRoot);

afterEach(() => {
  vi.unstubAllGlobals();
  resetRoot();
});

describe("themeScript", () => {
  it("is valid JavaScript", () => {
    expect(() => new Function(themeScript)).not.toThrow();
  });

  it("applies a stored dark theme over a light system preference", () => {
    stubPrefersDark(false);
    localStorage.setItem("nojoin-theme", "dark");

    runScript();

    expect(root.classList.contains("dark")).toBe(true);
  });

  it("applies a stored light theme over a dark system preference", () => {
    stubPrefersDark(true);
    root.classList.add("dark");
    localStorage.setItem("nojoin-theme", "light");

    runScript();

    expect(root.classList.contains("dark")).toBe(false);
  });

  it("follows the system preference when nothing is stored", () => {
    stubPrefersDark(true);

    runScript();

    expect(root.classList.contains("dark")).toBe(true);
  });

  it("falls back to the system preference when storage throws", () => {
    stubPrefersDark(true);
    vi.stubGlobal("localStorage", {
      getItem: () => {
        throw new Error("storage blocked");
      },
    });

    runScript();

    expect(root.classList.contains("dark")).toBe(true);
    expect(root.dataset.palette).toBeUndefined();
    expect(root.dataset.corners).toBeUndefined();
  });
});

describe("themeScript appearance preferences", () => {
  beforeEach(() => {
    stubPrefersDark(false);
  });

  it("leaves palette and corners unset by default so the original look applies", () => {
    runScript();

    expect(root.hasAttribute("data-palette")).toBe(false);
    expect(root.hasAttribute("data-corners")).toBe(false);
  });

  it("stamps a stored corner style", () => {
    localStorage.setItem("nojoin-corners", "square");

    runScript();

    expect(root.dataset.corners).toBe("square");
  });

  // Every palette the settings offer must survive the pre-paint script, or
  // choosing it flashes the default palette on each load.
  it.each(PALETTES.filter((palette) => palette !== DEFAULT_PALETTE))(
    "stamps the stored %s palette before first paint",
    (palette) => {
      localStorage.setItem("nojoin-palette", palette);

      runScript();

      expect(root.dataset.palette).toBe(palette);
    },
  );

  it("ignores values it does not recognise rather than stamping them", () => {
    localStorage.setItem("nojoin-palette", "neon\"><script>");
    localStorage.setItem("nojoin-corners", "blobby");
    localStorage.setItem("nojoin-density", "tiny");
    setViewport(390, 844);

    runScript();

    expect(root.hasAttribute("data-palette")).toBe(false);
    expect(root.hasAttribute("data-corners")).toBe(false);
    expect(root.dataset.uiDensity).toBe("comfortable");
  });

  it("applies an explicit density whatever the viewport", () => {
    localStorage.setItem("nojoin-density", "compact");
    setViewport(390, 844);

    runScript();

    expect(root.dataset.uiDensity).toBe("compact");
  });

  it.each([
    [390, 844],
    [1023, 700],
    [1024, 768],
    [1440, 900],
    [1920, 1080],
    [1920, 1081],
    [2560, 1440],
  ])("resolves automatic density at %ix%i exactly as the React provider does", (width, height) => {
    setViewport(width, height);

    runScript();

    expect(root.dataset.uiDensity).toBe(resolveDensity("auto", width, height));
  });
});
