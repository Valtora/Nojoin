"use client";

import {
  RefObject,
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
} from "react";

import { TranscriptSegment } from "@/types";

export interface UseTranscriptScrollOptions {
  segments: TranscriptSegment[];
  /** Key of the segment currently aligned with playback, if any. */
  activeSegmentKey: string | null;
}

export interface TranscriptScroll {
  scrollContainerRef: RefObject<HTMLDivElement | null>;
  activeSegmentRef: RefObject<HTMLDivElement | null>;
  scrollSegmentIntoView: (
    segmentKey: string,
    behavior?: ScrollBehavior,
  ) => void;
  updateScrollAnchor: () => void;
}

/**
 * Owns the transcript scroll container refs and the scroll-anchoring behaviour
 * for {@link TranscriptView} (FE-012): centering the active/searched segment and
 * preserving the viewport when older utterances are inserted ahead of it.
 * Lifted verbatim from the component so the measured scroll math is unchanged.
 */
export function useTranscriptScroll(
  options: UseTranscriptScrollOptions,
): TranscriptScroll {
  const { segments, activeSegmentKey } = options;

  const activeSegmentRef = useRef<HTMLDivElement>(null);
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const scrollAnchorRef = useRef<{
    segmentId: string;
    offset: number;
    orderIndex: number;
  } | null>(null);

  const scrollSegmentIntoView = useCallback(
    (segmentKey: string, behavior: ScrollBehavior = "smooth") => {
      const container = scrollContainerRef.current;
      const element = document.getElementById(`segment-${segmentKey}`);
      if (!container || !element) {
        return;
      }

      const elementTop = element.offsetTop;
      const elementBottom = elementTop + element.offsetHeight;
      const visibleTop = container.scrollTop;
      const visibleBottom = visibleTop + container.clientHeight;

      if (elementTop >= visibleTop && elementBottom <= visibleBottom) {
        return;
      }

      const centeredTop = Math.max(
        0,
        elementTop - container.clientHeight / 2 + element.offsetHeight / 2,
      );
      container.scrollTo({ top: centeredTop, behavior });
    },
    [],
  );

  const updateScrollAnchor = useCallback(() => {
    const container = scrollContainerRef.current;
    if (!container) {
      return;
    }

    const segmentElements = Array.from(
      container.querySelectorAll<HTMLElement>("[data-segment-id]"),
    );
    if (segmentElements.length === 0) {
      scrollAnchorRef.current = null;
      return;
    }

    const firstVisible =
      segmentElements.find(
        (element) =>
          element.offsetTop + element.offsetHeight > container.scrollTop,
      ) || segmentElements[segmentElements.length - 1];
    const segmentId = firstVisible?.dataset.segmentId;
    if (!firstVisible || !segmentId) {
      return;
    }

    scrollAnchorRef.current = {
      segmentId,
      offset: firstVisible.offsetTop - container.scrollTop,
      orderIndex: Number(firstVisible.dataset.orderIndex ?? 0),
    };
  }, []);

  useEffect(() => {
    if (activeSegmentRef.current) {
      const segmentKey = activeSegmentRef.current.dataset.segmentId;
      if (segmentKey) {
        scrollSegmentIntoView(segmentKey, "smooth");
      }
    }
  }, [activeSegmentKey, scrollSegmentIntoView]);

  useLayoutEffect(() => {
    const container = scrollContainerRef.current;
    const anchor = scrollAnchorRef.current;
    if (!container || !anchor) {
      return;
    }

    const segmentElements = Array.from(
      container.querySelectorAll<HTMLElement>("[data-segment-id]"),
    );
    if (segmentElements.length === 0) {
      return;
    }

    const anchorElement =
      segmentElements.find(
        (element) => element.dataset.segmentId === anchor.segmentId,
      ) ||
      segmentElements.find(
        (element) =>
          Number(element.dataset.orderIndex ?? -1) >= anchor.orderIndex,
      ) ||
      segmentElements[segmentElements.length - 1];
    if (!anchorElement) {
      return;
    }

    const nextScrollTop = Math.max(0, anchorElement.offsetTop - anchor.offset);
    if (Math.abs(container.scrollTop - nextScrollTop) > 1) {
      container.scrollTop = nextScrollTop;
    }
  }, [segments]);

  useEffect(() => {
    updateScrollAnchor();
  }, [segments, updateScrollAnchor]);

  return {
    scrollContainerRef,
    activeSegmentRef,
    scrollSegmentIntoView,
    updateScrollAnchor,
  };
}
