import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { act, render, screen } from "@testing-library/react";

import { ViewportDensityProvider, useViewportDensity } from "./ViewportDensityProvider";

/**
 * Rails and panels narrow themselves on `isCompact` (MainNav, Sidebar, the
 * recording layout). Dense is tighter than compact, so it has to count as
 * compact there, or choosing Dense would widen the rails back out.
 */
function Probe() {
  const { density, isCompact } = useViewportDensity();
  return (
    <p>
      {density}:{String(isCompact)}
    </p>
  );
}

beforeEach(() => {
  vi.stubGlobal("innerWidth", 2560);
  vi.stubGlobal("innerHeight", 1440);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("ViewportDensityProvider", () => {
  it.each([
    [null, "comfortable:false"],
    ["compact", "compact:true"],
    ["dense", "dense:true"],
  ])("with stored preference %s on a large monitor reports %s", (stored, expected) => {
    if (stored) {
      localStorage.setItem("nojoin-density", stored);
    }

    render(
      <ViewportDensityProvider>
        <Probe />
      </ViewportDensityProvider>,
    );

    expect(screen.getByText(expected)).toBeInTheDocument();
  });

  it.each(["comfortable", "compact", "dense"])(
    "keeps an explicit %s density through window resizes",
    (density) => {
      localStorage.setItem("nojoin-density", density);
      render(
        <ViewportDensityProvider>
          <Probe />
        </ViewportDensityProvider>,
      );

      for (const [width, height] of [[390, 844], [1440, 900], [2560, 1440]]) {
        vi.stubGlobal("innerWidth", width);
        vi.stubGlobal("innerHeight", height);
        act(() => {
          window.dispatchEvent(new Event("resize"));
        });

        expect(document.documentElement.dataset.uiDensity).toBe(density);
      }
    },
  );

  it("re-resolves automatic density on resize", () => {
    render(
      <ViewportDensityProvider>
        <Probe />
      </ViewportDensityProvider>,
    );
    expect(document.documentElement.dataset.uiDensity).toBe("comfortable");

    vi.stubGlobal("innerWidth", 1440);
    vi.stubGlobal("innerHeight", 900);
    act(() => {
      window.dispatchEvent(new Event("resize"));
    });

    expect(document.documentElement.dataset.uiDensity).toBe("compact");
  });
});
