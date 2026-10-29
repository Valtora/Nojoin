"use client";

import { useEffect, type KeyboardEvent, type RefObject } from "react";

/** What Tab moves between in a panel that holds an edit. */
const TAB_STOPS = "input, select, textarea, button:not(:disabled)";

interface PanelDismissal {
  /** The open panel, portalled to the end of the page. */
  panelRef: RefObject<HTMLElement | null>;
  /** What the panel sits against; a press on it is not a press outside. */
  anchor: HTMLElement;
  /** The edit is as it opened, so letting it go loses nothing. */
  unchanged: boolean;
  /** Closes the panel and leaves focus where a press outside put it. */
  dismiss: () => void;
  /** Closes the panel and puts focus back on the anchor. */
  close: () => void;
}

/**
 * How an anchored panel holding an edit lets go. Escape closes it from
 * anywhere in it. A press outside closes it only while the edit is as it
 * opened, so a stray tap never throws an edit away. A Tab out of it, which
 * would go on from the end of the page where it sits, closes it and carries on
 * from the anchor instead, while the edit is as it opened; once changed, Tab
 * wraps.
 *
 * Returns the panel's keydown handler.
 */
export function usePanelDismissal({ panelRef, anchor, unchanged, dismiss, close }: PanelDismissal) {
  useEffect(() => {
    if (!unchanged) return;
    const closeOnOutsidePress = (event: PointerEvent) => {
      const target = event.target as Node;
      if (panelRef.current?.contains(target) || anchor.contains(target)) return;
      dismiss();
    };
    document.addEventListener("pointerdown", closeOnOutsidePress, true);
    return () => document.removeEventListener("pointerdown", closeOnOutsidePress, true);
  }, [unchanged, anchor, dismiss, panelRef]);

  return (event: KeyboardEvent<HTMLElement>) => {
    if (event.nativeEvent.isComposing || event.keyCode === 229) return;
    if (event.key === "Escape") {
      event.preventDefault();
      close();
      return;
    }
    if (event.key !== "Tab") return;
    const stops = Array.from(panelRef.current?.querySelectorAll<HTMLElement>(TAB_STOPS) ?? []);
    const edge = event.shiftKey ? stops[0] : stops[stops.length - 1];
    if (document.activeElement !== edge) return;
    if (unchanged) {
      close();
      return;
    }
    event.preventDefault();
    (event.shiftKey ? stops[stops.length - 1] : stops[0])?.focus();
  };
}
