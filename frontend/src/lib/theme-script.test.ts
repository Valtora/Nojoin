import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { themeScript } from "./theme-script";

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

const root = document.documentElement;

beforeEach(() => {
  root.className = "";
});

afterEach(() => {
  vi.unstubAllGlobals();
  root.className = "";
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
  });
});
