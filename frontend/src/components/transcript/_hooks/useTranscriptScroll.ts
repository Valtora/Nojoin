"use client";

import {
  RefObject,
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";

import { TranscriptSegment } from "@/types";

/**
 * A line's row. Its text paragraph names the same line in `data-segment-id`
 * too; the row alone carries `data-order-index`, and anchors the scroll.
 */
const LINE_ROWS = "[data-order-index]";

export interface UseTranscriptScrollOptions {
  segments: TranscriptSegment[];
  /** Key of the shown line playback-follow keeps in view, if any. */
  activeSegmentKey: string | null;
  /** Follows the in-flight transcript's tail until the reader scrolls away. */
  liveFollow?: boolean;
  /** Other content, such as inline notes, which can change the tail's height. */
  growth?: unknown;
}

export interface TranscriptScroll {
  scrollContainerRef: RefObject<HTMLDivElement | null>;
  scrollSegmentIntoView: (
    segmentKey: string,
    behavior?: ScrollBehavior,
  ) => void;
  updateScrollAnchor: () => void;
  isFollowingLatest: boolean;
  jumpToLatest: () => void;
}

/**
 * Owns the transcript scroll container refs and the scroll-anchoring behaviour
 * for {@link TranscriptView}: centering the active or searched segment, and
 * preserving the viewport when older utterances are inserted ahead of it.
 */
export function useTranscriptScroll(
  options: UseTranscriptScrollOptions,
): TranscriptScroll {
  const { segments, activeSegmentKey, liveFollow = false, growth } = options;

  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const followingRef = useRef(true);
  const [isFollowingLatest, setIsFollowingLatest] = useState(true);
  const previousFollowMode = useRef(liveFollow);
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

    if (liveFollow) {
      const following = container.scrollHeight - container.scrollTop - container.clientHeight <= 32;
      followingRef.current = following;
      setIsFollowingLatest(following);
    }

    const segmentElements = Array.from(
      container.querySelectorAll<HTMLElement>(LINE_ROWS),
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
  }, [liveFollow]);

  const jumpToLatest = useCallback(() => {
    const container = scrollContainerRef.current;
    followingRef.current = true;
    setIsFollowingLatest(true);
    if (container) container.scrollTop = container.scrollHeight;
  }, []);

  // Follows the line by its key. Overlapping lines can be active together, and
  // a line that stops being active doesn't move this to one that still is.
  useEffect(() => {
    const leavingLive = previousFollowMode.current && !liveFollow;
    previousFollowMode.current = liveFollow;
    // Handoff alone does not start playback: its idle clock may still be at
    // the first line while the reader is looking at the transcript's tail.
    if (activeSegmentKey && !liveFollow && !leavingLive) {
      scrollSegmentIntoView(activeSegmentKey, "smooth");
    }
  }, [activeSegmentKey, liveFollow, scrollSegmentIntoView]);

  useLayoutEffect(() => {
    const container = scrollContainerRef.current;
    const anchor = scrollAnchorRef.current;
    if (container && liveFollow && followingRef.current) {
      container.scrollTop = container.scrollHeight;
      return;
    }
    if (!container || !anchor) {
      return;
    }

    const segmentElements = Array.from(
      container.querySelectorAll<HTMLElement>(LINE_ROWS),
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
  }, [segments, liveFollow, growth]);

  useEffect(() => {
    updateScrollAnchor();
  }, [segments, growth, updateScrollAnchor]);

  return {
    scrollContainerRef,
    scrollSegmentIntoView,
    updateScrollAnchor,
    isFollowingLatest,
    jumpToLatest,
  };
}
