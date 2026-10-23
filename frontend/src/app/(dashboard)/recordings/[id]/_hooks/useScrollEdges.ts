import { useEffect, useState, type RefObject } from "react";

export interface ScrollEdges {
  /** Content is scrolled out of view past the left edge. */
  left: boolean;
  /** Content runs on past the right edge. */
  right: boolean;
}

const NONE: ScrollEdges = { left: false, right: false };

/**
 * Which sides of a sideways-scrolling element have content out of view.
 * Recomputed on scroll, and on resize of the element or its children (rotation,
 * font changes); a 1 px tolerance absorbs fractional scroll positions.
 */
export function useScrollEdges(
  ref: RefObject<HTMLElement | null>,
  enabled: boolean,
): ScrollEdges {
  const [edges, setEdges] = useState<ScrollEdges>(NONE);

  useEffect(() => {
    const row = ref.current;
    if (!enabled || !row) return;
    const update = () => {
      const left = row.scrollLeft > 1;
      const right = row.scrollLeft + row.clientWidth < row.scrollWidth - 1;
      setEdges((current) =>
        current.left === left && current.right === right
          ? current
          : { left, right },
      );
    };
    update();
    row.addEventListener("scroll", update, { passive: true });
    const observer = new ResizeObserver(update);
    observer.observe(row);
    for (const child of row.children) observer.observe(child);
    return () => {
      row.removeEventListener("scroll", update);
      observer.disconnect();
    };
  }, [ref, enabled]);

  return enabled ? edges : NONE;
}
