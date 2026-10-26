"use client";

import { useCallback, useEffect, useRef, type RefObject } from "react";

/** How far above the bottom, in px, still counts as reading the newest line. */
export const NEAR_BOTTOM_PX = 32;

/**
 * Keeps a scrolling list on its newest content while the reader is at or near
 * the bottom, and leaves it where it is once they scroll up to read.
 *
 * A ResizeObserver on the list and on `content`, the element holding its
 * items, re-pins after anything that changes either size: history arriving and
 * its Markdown rendering, a new message, a streamed token, or the list's pane
 * settling or being dragged. Pins are instant, so nothing can cut the landing
 * short, except growth of the content after the reader sends a message, which
 * glides unless they prefer reduced motion. Only the list scrolls:
 * scrollIntoView would move every scrollable ancestor too, the app shell
 * included.
 *
 * `content` is the element itself rather than a ref, so a new one (a new
 * recording's history, the first message after a clear) lands at the bottom
 * afresh. Call the returned function when the reader sends a message: it
 * sticks the list again, even if they had scrolled up, and lets what follows
 * glide in.
 */
export function useStickToBottom(
  listRef: RefObject<HTMLElement | null>,
  content: HTMLElement | null,
): () => void {
  const stuck = useRef(true);
  const glides = useRef(false);

  useEffect(() => {
    const list = listRef.current;
    if (!list || !content) return;

    stuck.current = true;
    let lastTop = list.scrollTop;

    const onScroll = () => {
      const top = list.scrollTop;
      const fromBottom = list.scrollHeight - list.clientHeight - top;
      // Any move up off the bottom is the reader's, so a stream never pulls
      // them back: a pin only moves the list down, and a list clamped by a
      // resize or by shrinking content ends at the bottom.
      if (top < lastTop && fromBottom > 1) stuck.current = false;
      else if (fromBottom <= NEAR_BOTTOM_PX) stuck.current = true;
      lastTop = top;
    };

    const observer = new ResizeObserver((entries) => {
      if (!stuck.current) return;
      const glide =
        glides.current &&
        entries.every((entry) => entry.target === content) &&
        !window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
      list.scrollTo({ top: list.scrollHeight, behavior: glide ? "smooth" : "instant" });
    });

    list.addEventListener("scroll", onScroll, { passive: true });
    observer.observe(list);
    observer.observe(content);
    return () => {
      list.removeEventListener("scroll", onScroll);
      observer.disconnect();
      // Whatever replaces this content lands rather than glides.
      glides.current = false;
    };
  }, [listRef, content]);

  return useCallback(() => {
    stuck.current = true;
    glides.current = true;
  }, []);
}
