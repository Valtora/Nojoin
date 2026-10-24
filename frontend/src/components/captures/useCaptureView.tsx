"use client";

import { useCallback, useMemo, useState, type ReactNode } from "react";

import type { Capture } from "@/lib/api";
import type { CapturesState } from "@/lib/captures/useCaptures";

import CaptureViewer from "./CaptureViewer";
import { captureItems, type CaptureItem } from "./captureItems";

const NO_ITEMS: readonly CaptureItem[] = [];

interface CaptureView {
  /** Saved and unsaved captures together, in timeline order, for the markers. */
  items: readonly CaptureItem[];
  /** Opens a saved capture full size. */
  open: (capture: Capture) => void;
  /** The viewer, to render once wherever the view is. */
  viewer: ReactNode;
}

/**
 * The marker items and the full-size viewer one view of a recording shares.
 * The viewer follows the list, so a switch turned or a parse finished shows
 * in it at once, and it closes if its capture goes.
 */
export function useCaptureView(
  state: CapturesState | undefined,
  options: { onPlay?: (seconds: number) => void } = {},
): CaptureView {
  const [openId, setOpenId] = useState<number | null>(null);
  const captures = state?.captures;
  const pending = state?.pending;
  // On the lists, not the state object, which is new on every render: the
  // transcript's lines are memoised on what this returns.
  const items = useMemo(
    () => (captures || pending?.length ? captureItems(captures, pending) : NO_ITEMS),
    [captures, pending],
  );
  const shown = captures?.find((capture) => capture.id === openId) ?? null;
  const open = useCallback((capture: Capture) => setOpenId(capture.id), []);
  const close = useCallback(() => setOpenId(null), []);
  const viewer = state ? (
    <CaptureViewer
      capture={shown}
      onClose={close}
      onSetUseInNotes={state.setUseInNotes}
      onDelete={state.remove}
      onPlay={options.onPlay}
      onChanged={state.refresh}
    />
  ) : null;
  return { items, open, viewer };
}
