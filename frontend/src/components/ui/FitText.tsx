"use client";

import {
  useCallback,
  useLayoutEffect,
  useRef,
  useState,
} from "react";

import { cn } from "@/lib/cn";

interface FitTextProps {
  /** Designed size, in rem, used whenever the text fits on one line. */
  maxRem: number;
  /** Readability floor, in rem. Below it the text wraps instead of shrinking. */
  minRem: number;
  children: string;
  className?: string;
}

/**
 * Shrinks a single line of text to fit its container's width, stepping the
 * font down from maxRem towards minRem. CSS alone cannot do this: clamp()
 * tracks the container's size, never the text's own length. When even minRem
 * cannot fit the line, the text wraps and clamps to two lines instead — the
 * floor is a readability contract, not a truncation point.
 */
export default function FitText({
  maxRem,
  minRem,
  children,
  className,
}: FitTextProps) {
  const containerRef = useRef<HTMLSpanElement>(null);
  const measureRef = useRef<HTMLSpanElement>(null);
  const [fontRem, setFontRem] = useState(maxRem);
  const [wraps, setWraps] = useState(false);

  const fit = useCallback(() => {
    const container = containerRef.current;
    const measure = measureRef.current;
    if (!container || !measure) return;
    const available = container.clientWidth;
    const needed = measure.scrollWidth;
    // jsdom and the pre-paint frame both report zero; keep the designed size.
    if (available <= 0 || needed <= 0) {
      setFontRem(maxRem);
      setWraps(false);
      return;
    }
    const scaled = maxRem * (available / needed);
    if (scaled >= maxRem) {
      setFontRem(maxRem);
      setWraps(false);
    } else if (scaled >= minRem) {
      setFontRem(scaled);
      setWraps(false);
    } else {
      setFontRem(minRem);
      setWraps(true);
    }
  }, [maxRem, minRem]);

  useLayoutEffect(() => {
    fit();
    const container = containerRef.current;
    if (!container || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(fit);
    observer.observe(container);
    return () => observer.disconnect();
  }, [fit, children]);

  return (
    <span
      ref={containerRef}
      className={cn("relative block w-full min-w-0", className)}
    >
      {/* Invisible copy at the designed size gives the single-line width the
          fit calculation needs without disturbing layout. */}
      <span
        ref={measureRef}
        aria-hidden="true"
        className="pointer-events-none invisible absolute whitespace-nowrap"
        style={{ fontSize: `${maxRem}rem` }}
      >
        {children}
      </span>
      <span
        className={
          wraps
            ? "block break-words line-clamp-2"
            : "block truncate whitespace-nowrap"
        }
        style={{ fontSize: `${fontRem}rem` }}
      >
        {children}
      </span>
    </span>
  );
}
