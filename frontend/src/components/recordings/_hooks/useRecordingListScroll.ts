"use client";

import { useCallback, useEffect, useLayoutEffect, useRef, type RefObject } from "react";

import type { RecordingId } from "@/types";

/** The card at the top of the list, and how far its top sits from the list's. */
interface ListPlace {
  id: RecordingId;
  offset: number;
}

const cardsIn = (scroller: HTMLElement) =>
  Array.from(scroller.querySelectorAll<HTMLElement>("[data-recording-link]"));

/** The first card that reaches below `top`. The cards run top to bottom, so a binary search finds it. */
function firstCardBelow(cards: HTMLElement[], top: number): HTMLElement | null {
  let low = 0;
  let high = cards.length - 1;
  let found: HTMLElement | null = null;
  while (low <= high) {
    const middle = (low + high) >> 1;
    if (cards[middle].getBoundingClientRect().bottom > top) {
      found = cards[middle];
      high = middle - 1;
    } else {
      low = middle + 1;
    }
  }
  return found;
}

/**
 * Where the recordings list is scrolled to, kept while the rail is collapsed
 * (#974). Collapsing takes the list out of the page, so as it scrolls the
 * list notes the card at its top, and puts that card back when it returns.
 *
 * It keeps a card rather than a distance: the list comes back at the
 * collapsed rail's width and widens with the rail, which changes every
 * card's height, so the card is put back again whenever the list's size
 * changes, as the transcript keeps its first line in place.
 *
 * A new search, filter or view (`resetKey`) starts at the top of its results.
 * Left at the end of the old list, the scroller would stay at the end of the
 * new one and the footer would load page after page.
 *
 * Returns the scroller's `onScroll` handler.
 */
export function useRecordingListScroll(
  scrollRef: RefObject<HTMLElement | null>,
  { shown, resetKey }: { shown: boolean; resetKey: string },
): () => void {
  const place = useRef<ListPlace | null>(null);

  const notePlace = useCallback(() => {
    const scroller = scrollRef.current;
    if (!scroller) return;
    const top = scroller.getBoundingClientRect().top;
    const card = firstCardBelow(cardsIn(scroller), top);
    const id = card?.dataset.recordingLink;
    place.current =
      card && id ? { id, offset: card.getBoundingClientRect().top - top } : null;
  }, [scrollRef]);

  const restorePlace = useCallback(() => {
    const scroller = scrollRef.current;
    const kept = place.current;
    if (!scroller || !kept) return;
    const card = cardsIn(scroller).find(
      (element) => element.dataset.recordingLink === kept.id,
    );
    if (!card) return;
    const shift =
      card.getBoundingClientRect().top - scroller.getBoundingClientRect().top - kept.offset;
    if (Math.abs(shift) > 1) scroller.scrollTop += shift;
  }, [scrollRef]);

  useEffect(() => {
    place.current = null;
    if (scrollRef.current) scrollRef.current.scrollTop = 0;
  }, [resetKey, scrollRef]);

  useLayoutEffect(() => {
    const scroller = scrollRef.current;
    if (!shown || !scroller) return;
    restorePlace();
    const observer = new ResizeObserver(restorePlace);
    observer.observe(scroller);
    return () => observer.disconnect();
  }, [shown, restorePlace, scrollRef]);

  return notePlace;
}
