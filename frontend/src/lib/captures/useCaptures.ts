"use client";

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";

import {
  deleteDocument,
  getCaptures,
  setCaptureUseInNotes,
  type Capture,
} from "@/lib/api";
import { notifyRequestError } from "@/lib/notifyRequestError";
import type { RecordingId } from "@/types";

import { captureOutbox, type PendingCapture } from "./captureOutbox";

/** How often a page with a capture still being read asks again. */
export const CAPTURES_PARSE_POLL_MS = 4_000;
/** How long to wait before asking again after a failed request. */
export const CAPTURES_RETRY_MS = 3_000;

export interface CapturesState {
  /** The saved captures in timeline order; null while they load. */
  captures: readonly Capture[] | null;
  /** Captures still on their way to the server, from this page. */
  pending: readonly PendingCapture[];
  refresh: () => void;
  setUseInNotes: (capture: Capture, useInNotes: boolean) => Promise<void>;
  /** Deletes a saved capture; resolves false if the server refused. */
  remove: (capture: Capture) => Promise<boolean>;
  retry: (key: string) => void;
  discard: (key: string) => void;
}

const isBeingRead = (capture: Capture) =>
  capture.status === "PENDING" || capture.status === "PROCESSING";

const byTime = (left: Capture, right: Capture) =>
  left.capture_ms - right.capture_ms ||
  left.created_at.localeCompare(right.created_at) ||
  left.id - right.id;

const NO_PENDING: readonly PendingCapture[] = Object.freeze([]);
const subscribeToNothing = () => () => undefined;

/**
 * A recording's captures: fetched when the page opens it, again whenever a
 * read of the recording carries a new `captures_signature`, every few seconds
 * while one is still being read (a finished meeting's page does not poll),
 * and at once when this page's own upload lands. A notes switch shows its new
 * position at once and keeps it while the change saves.
 */
export function useCaptures(
  recordingId: RecordingId | null,
  signature: string | null | undefined,
): CapturesState {
  const [loaded, setLoaded] = useState<{
    recordingId: RecordingId;
    captures: Capture[];
  } | null>(null);
  // Switches turned here and not yet saved, by capture id.
  const [switching, setSwitching] = useState<ReadonlyMap<number, boolean>>(new Map());
  const latestRequest = useRef(0);
  const [retries, setRetries] = useState(0);

  const fetchList = useCallback((id: RecordingId) => {
    latestRequest.current += 1;
    const request = latestRequest.current;
    getCaptures(id).then(
      (captures) => {
        if (request !== latestRequest.current) return;
        setLoaded({ recordingId: id, captures: [...captures].sort(byTime) });
      },
      () => {
        if (request !== latestRequest.current) return;
        setTimeout(() => {
          if (request === latestRequest.current) setRetries((count) => count + 1);
        }, CAPTURES_RETRY_MS);
      },
    );
  }, []);

  useEffect(() => {
    if (recordingId) fetchList(recordingId);
  }, [recordingId, signature, retries, fetchList]);

  // An answer arriving after the page has gone is dropped.
  useEffect(
    () => () => {
      latestRequest.current += 1;
    },
    [],
  );

  const current = loaded?.recordingId === recordingId ? loaded.captures : null;
  const anyBeingRead = current?.some(isBeingRead) ?? false;
  useEffect(() => {
    if (!recordingId || !anyBeingRead) return;
    const timer = setInterval(() => fetchList(recordingId), CAPTURES_PARSE_POLL_MS);
    return () => clearInterval(timer);
  }, [recordingId, anyBeingRead, fetchList]);

  // This page's own upload shows as soon as the server has it.
  useEffect(
    () =>
      captureOutbox.onUploaded((capture) => {
        if (capture.recording_id !== recordingId) return;
        setLoaded((previous) =>
          previous && previous.recordingId === recordingId
            ? {
                recordingId,
                captures: [
                  ...previous.captures.filter((item) => item.id !== capture.id),
                  capture,
                ].sort(byTime),
              }
            : previous,
        );
        fetchList(recordingId);
      }),
    [recordingId, fetchList],
  );

  const pending = useSyncExternalStore(
    recordingId ? captureOutbox.subscribe : subscribeToNothing,
    () => (recordingId ? captureOutbox.pending(recordingId) : NO_PENDING),
    () => NO_PENDING,
  );

  const setUseInNotes = useCallback(
    async (capture: Capture, useInNotes: boolean) => {
      setSwitching((previous) => new Map(previous).set(capture.id, useInNotes));
      try {
        const saved = await setCaptureUseInNotes(capture.recording_id, capture.id, useInNotes);
        setLoaded((previous) =>
          previous && previous.recordingId === saved.recording_id
            ? {
                ...previous,
                captures: previous.captures.map((item) => (item.id === saved.id ? saved : item)),
              }
            : previous,
        );
      } catch (error: unknown) {
        notifyRequestError(error, "The capture's notes setting could not be saved.");
      } finally {
        setSwitching((previous) => {
          const next = new Map(previous);
          next.delete(capture.id);
          return next;
        });
      }
    },
    [],
  );

  const remove = useCallback(async (capture: Capture) => {
    try {
      await deleteDocument(capture.id);
    } catch (error: unknown) {
      notifyRequestError(error, "The capture could not be deleted.");
      return false;
    }
    setLoaded((previous) =>
      previous
        ? { ...previous, captures: previous.captures.filter((item) => item.id !== capture.id) }
        : previous,
    );
    return true;
  }, []);

  const refresh = useCallback(() => {
    if (recordingId) fetchList(recordingId);
  }, [recordingId, fetchList]);

  const captures = useMemo(
    () =>
      current === null || switching.size === 0
        ? current
        : current.map((capture) =>
            switching.has(capture.id)
              ? { ...capture, use_in_notes: switching.get(capture.id) === true }
              : capture,
          ),
    [current, switching],
  );

  return {
    captures,
    pending,
    refresh,
    setUseInNotes,
    remove,
    retry: captureOutbox.retry,
    discard: captureOutbox.discard,
  };
}
