"use client";

import { type RefObject, useCallback, useLayoutEffect, useRef, useState } from "react";

/**
 * Anchors a floating panel to a trigger with viewport-relative positioning.
 *
 * A panel positioned the ordinary way -- `absolute`, inside the element that
 * opened it -- belongs to whatever scroll box encloses it. Inside a modal that
 * box is the panel body, and the modal hides its own overflow, so a dropdown
 * opened near the foot of a long form renders into the region the modal clips
 * and appears not to have opened at all. It is still there, and scrolling
 * reaches it, but the user has no way to know that. A panel wider than the
 * modal's content box loses its right edge outright, with no scroll to recover
 * it.
 *
 * `position: fixed` takes the panel out of that box and measures it against the
 * window instead, which is the same reason ModernDatePicker asks react-datepicker
 * for the fixed strategy. The panel stays where it is in the DOM, so a focus
 * trap still contains it and Headless UI does not read a click inside it as a
 * click outside the dialog -- both of which portalling to the body would break.
 *
 * The arithmetic is the one SpeakerAssignmentPopover arrived at: prefer below
 * the trigger, flip above when below cannot hold the panel and above is roomier,
 * cap the height to whichever side was chosen so the content scrolls inside the
 * panel rather than off the screen, and clamp both axes to the viewport.
 */

const GAP = 8; // between trigger and panel
const MARGIN = 8; // smallest gap to a viewport edge
const MIN_HEIGHT = 120; // below this a panel is not worth showing shorter

export interface AnchoredPanelStyle {
  position: "fixed";
  top: number;
  left: number;
  /** Absent on the measuring pass, which has to see the panel's natural size. */
  maxHeight?: number;
  maxWidth?: number;
  /** Only when matchAnchorWidth is asked for. */
  width?: number;
  visibility: "hidden" | "visible";
}

interface AnchoredPanelOptions {
  /** Size the panel to its trigger, for a dropdown that used to be `w-full`
   *  inside it. Fixed positioning takes that percentage away. */
  matchAnchorWidth?: boolean;
}

const clamp = (value: number, min: number, max: number) =>
  Math.min(Math.max(value, min), Math.max(min, max));

export function useAnchoredPanel<Panel extends HTMLElement = HTMLElement>(
  open: boolean,
  anchorRef: RefObject<HTMLElement | null>,
  { matchAnchorWidth = false }: AnchoredPanelOptions = {},
) {
  const panelRef = useRef<Panel>(null);
  const [style, setStyle] = useState<AnchoredPanelStyle | null>(null);

  const reposition = useCallback(() => {
    const anchor = anchorRef.current;
    const panel = panelRef.current;
    if (!anchor || !panel) return;

    const rect = anchor.getBoundingClientRect();
    const viewportWidth = window.innerWidth;
    const viewportHeight = window.innerHeight;

    // scrollHeight, not offsetHeight: once a max-height is applied the panel
    // stops reporting what it actually wants, and the placement would then
    // oscillate between the two sides on every reposition.
    const wantedHeight = panel.scrollHeight;
    const width = Math.min(
      matchAnchorWidth ? rect.width : panel.offsetWidth,
      viewportWidth - MARGIN * 2,
    );

    const availableBelow = Math.max(0, viewportHeight - rect.bottom - GAP - MARGIN);
    const availableAbove = Math.max(0, rect.top - GAP - MARGIN);
    const placeAbove = availableBelow < wantedHeight && availableAbove > availableBelow;

    const maxHeight = Math.max(
      MIN_HEIGHT,
      placeAbove ? availableAbove : availableBelow,
    );
    const renderedHeight = Math.min(wantedHeight, maxHeight);

    setStyle({
      position: "fixed",
      top: clamp(
        placeAbove ? rect.top - GAP - renderedHeight : rect.bottom + GAP,
        MARGIN,
        viewportHeight - renderedHeight - MARGIN,
      ),
      left: clamp(rect.left, MARGIN, viewportWidth - width - MARGIN),
      maxHeight,
      maxWidth: viewportWidth - MARGIN * 2,
      ...(matchAnchorWidth ? { width } : {}),
      visibility: "visible",
    });
  }, [anchorRef, matchAnchorWidth]);

  useLayoutEffect(() => {
    if (!open) {
      setStyle(null);
      return;
    }
    reposition();
    window.addEventListener("resize", reposition);
    // Capture phase: the scroll that moves the trigger is the modal body's, not
    // the window's, and a scroll event does not bubble.
    window.addEventListener("scroll", reposition, true);
    return () => {
      window.removeEventListener("resize", reposition);
      window.removeEventListener("scroll", reposition, true);
    };
  }, [open, reposition]);

  return {
    panelRef,
    /** Spread onto the panel. The measuring pass is hidden and deliberately
     *  unconstrained: capping it there would have the panel measure the size it
     *  was given rather than the size it wants. useLayoutEffect replaces this
     *  before the browser paints, so nothing flashes. */
    panelStyle: style ?? {
      position: "fixed" as const,
      top: 0,
      left: 0,
      visibility: "hidden" as const,
    },
    reposition,
  };
}
