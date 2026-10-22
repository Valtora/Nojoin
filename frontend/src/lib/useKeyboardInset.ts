"use client";

import { useEffect, useState } from "react";

/**
 * How many px at the bottom of the layout viewport the visual viewport does
 * not show: the on-screen keyboard, on browsers that overlay it rather than
 * resize the page (Chrome on Android by default, Safari on iOS). 0 without a
 * keyboard, and wherever the page is resized instead.
 *
 * A bottom-sticky element offset by this stays above the keyboard.
 */
export function useKeyboardInset(): number {
  const [inset, setInset] = useState(0);

  useEffect(() => {
    const viewport = typeof window === "undefined" ? null : window.visualViewport;
    if (!viewport) return;
    const update = () => {
      const covered = window.innerHeight - viewport.height - viewport.offsetTop;
      setInset(Math.max(0, Math.round(covered)));
    };
    update();
    viewport.addEventListener("resize", update);
    viewport.addEventListener("scroll", update);
    return () => {
      viewport.removeEventListener("resize", update);
      viewport.removeEventListener("scroll", update);
    };
  }, []);

  return inset;
}
