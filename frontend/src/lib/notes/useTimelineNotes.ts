"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { listTimelineNotes } from "@/lib/api";
import type { RecordingId, TimelineNote } from "@/types";

/** How long to wait before asking for the list again after a failed request. */
export const NOTES_RETRY_MS = 3_000;

interface Held {
  recordingId: RecordingId;
  /** Null when the list was fetched without a read of the recording before it. */
  signature: string | null;
}

/**
 * A recording's notes: fetched when the page opens it, and again whenever a
 * read of the recording carries a new `timeline_notes_list_signature`, rather
 * than every note riding on every poll.
 *
 * A list fetched in answer to a read is at least as new as that read, since
 * its request goes out after the read arrived, so the read's signature is the
 * list's. An answer to a request made before a later one is ignored. `refresh`
 * fetches it now, for a page that does not poll; the next signature then
 * fetches it once more. Null until the first list arrives.
 */
export function useTimelineNotes(
  recordingId: RecordingId | null,
  signature: string | null | undefined,
) {
  const [loaded, setLoaded] = useState<{
    recordingId: RecordingId;
    notes: TimelineNote[];
  } | null>(null);
  const held = useRef<Held | null>(null);
  const latestRequest = useRef(0);
  const [retries, setRetries] = useState(0);

  const fetchList = useCallback((id: RecordingId, forSignature: string | null) => {
    latestRequest.current += 1;
    const request = latestRequest.current;
    listTimelineNotes(id).then(
      (notes) => {
        if (request !== latestRequest.current) return;
        held.current = { recordingId: id, signature: forSignature };
        setLoaded({ recordingId: id, notes });
      },
      () => {
        if (request !== latestRequest.current) return;
        setTimeout(() => {
          if (request === latestRequest.current) setRetries((count) => count + 1);
        }, NOTES_RETRY_MS);
      },
    );
  }, []);

  useEffect(() => {
    if (!recordingId) return;
    const current = signature ?? null;
    const list = held.current;
    if (list && list.recordingId === recordingId && list.signature === current) return;
    fetchList(recordingId, current);
  }, [recordingId, signature, retries, fetchList]);

  // An answer arriving after the page has gone is dropped.
  useEffect(
    () => () => {
      latestRequest.current += 1;
    },
    [],
  );

  const refresh = useCallback(() => {
    if (recordingId) fetchList(recordingId, null);
  }, [recordingId, fetchList]);

  return {
    notes: loaded?.recordingId === recordingId ? loaded.notes : null,
    refresh,
  };
}
