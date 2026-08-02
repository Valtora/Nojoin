import { render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import FitText from "./FitText";

// jsdom performs no layout, so the sizes the fit calculation reads are
// simulated at the prototype level: clientWidth for the container,
// scrollWidth for the invisible measurement span.
const defineSizes = (clientWidth: number, scrollWidth: number) => {
  Object.defineProperty(HTMLElement.prototype, "clientWidth", {
    configurable: true,
    get() {
      return clientWidth;
    },
  });
  Object.defineProperty(HTMLElement.prototype, "scrollWidth", {
    configurable: true,
    get() {
      return scrollWidth;
    },
  });
};

afterEach(() => {
  defineSizes(0, 0);
});

const visibleSpan = (host: HTMLElement) => {
  const span = host.querySelector("span > span:not([aria-hidden])");
  expect(span).not.toBeNull();
  return span as HTMLElement;
};

describe("FitText", () => {
  it("keeps the designed size when no measurements are available", () => {
    const { container } = render(
      <FitText maxRem={1.5} minRem={1}>
        Quarterly Planning Sync
      </FitText>,
    );

    const span = visibleSpan(container);
    expect(span.style.fontSize).toBe("1.5rem");
    expect(span.className).toContain("truncate");
    expect(span.className).not.toContain("line-clamp-2");
  });

  it("keeps the designed size when the line already fits", () => {
    defineSizes(400, 300);
    const { container } = render(
      <FitText maxRem={1.5} minRem={1}>
        Short title
      </FitText>,
    );

    expect(visibleSpan(container).style.fontSize).toBe("1.5rem");
  });

  it("shrinks proportionally when the line overflows", () => {
    defineSizes(200, 300);
    const { container } = render(
      <FitText maxRem={1.5} minRem={1}>
        A title that is somewhat too long
      </FitText>,
    );

    const span = visibleSpan(container);
    // 1.5rem scaled by 200/300 = 1rem exactly, still at the floor boundary.
    expect(span.style.fontSize).toBe("1rem");
    expect(span.className).toContain("truncate");
  });

  it("floors at minRem and wraps to two lines when even the floor overflows", () => {
    defineSizes(100, 1000);
    const { container } = render(
      <FitText maxRem={1.5} minRem={1}>
        An exceedingly long meeting title that cannot fit on one line at any
        permitted size
      </FitText>,
    );

    const span = visibleSpan(container);
    expect(span.style.fontSize).toBe("1rem");
    expect(span.className).toContain("line-clamp-2");
    expect(span.className).not.toContain("truncate");
  });

  it("renders the measurement copy at the designed size, hidden from assistive tech", () => {
    const { container } = render(
      <FitText maxRem={2} minRem={1}>
        Measured
      </FitText>,
    );

    const measure = container.querySelector("span[aria-hidden]") as HTMLElement;
    expect(measure).not.toBeNull();
    expect(measure.style.fontSize).toBe("2rem");
    expect(measure.className).toContain("invisible");
  });
});
