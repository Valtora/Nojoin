"use client";

import { useCallback } from "react";
import { create } from "zustand";

/**
 * Bars at the foot of the screen that toasts rise above while they show, so a
 * notification never covers the controls in use: the recordings list's batch
 * bar, for one (#974). A bar joins with `useToastClearance`, and
 * `NotificationToast` measures each one against its stack.
 */
interface ToastClearanceState {
  bars: readonly HTMLElement[];
  add: (bar: HTMLElement) => void;
  remove: (bar: HTMLElement) => void;
}

export const useToastClearanceStore = create<ToastClearanceState>()((set) => ({
  bars: [],
  add: (bar) =>
    set((state) => (state.bars.includes(bar) ? state : { bars: [...state.bars, bar] })),
  remove: (bar) =>
    set((state) =>
      state.bars.includes(bar)
        ? { bars: state.bars.filter((kept) => kept !== bar) }
        : state,
    ),
}));

/** A ref for a bar that toasts keep clear of while it is in the page. */
export function useToastClearance<T extends HTMLElement>() {
  const add = useToastClearanceStore((state) => state.add);
  const remove = useToastClearanceStore((state) => state.remove);
  return useCallback(
    (bar: T | null) => {
      if (!bar) return;
      add(bar);
      return () => remove(bar);
    },
    [add, remove],
  );
}

/** Where toasts rest: 1rem above the foot of the screen. */
export const TOAST_REST_PX = 16;
/** The space left between a bar and the toasts raised above it. */
export const TOAST_BAR_GAP_PX = 8;

interface Box {
  top: number;
  bottom: number;
  left: number;
  right: number;
}

/**
 * How far the toast stack rises from where it rests: enough to clear every bar
 * it would otherwise cover, or nothing. A bar beside the stack, such as the
 * rail's batch bar on a desktop, where toasts sit at the bottom right, or one
 * higher up the screen than the stack reaches, leaves it where it is. A bar
 * with no height is not laid out and counts for nothing.
 */
export function toastLift(
  stack: { left: number; right: number; height: number },
  bars: readonly Box[],
  viewportHeight: number,
): number {
  const restBottom = viewportHeight - TOAST_REST_PX;
  const restTop = restBottom - stack.height;
  let lift = 0;
  for (const bar of bars) {
    if (bar.bottom <= bar.top) continue;
    const sharesColumns = bar.left < stack.right && bar.right > stack.left;
    const meetsStack = bar.top < restBottom && bar.bottom > restTop;
    if (sharesColumns && meetsStack) {
      lift = Math.max(lift, restBottom - bar.top + TOAST_BAR_GAP_PX);
    }
  }
  return lift;
}
