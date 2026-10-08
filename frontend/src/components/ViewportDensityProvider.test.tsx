import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { render, screen } from "@testing-library/react";

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
});
