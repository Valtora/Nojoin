"use client";

import { useCallback, useEffect, useState } from "react";

import { Document, getDocuments } from "@/lib/api";
import type { RecordingId } from "@/types";

const POLL_INTERVAL_MS = 4000;

/**
 * Documents attached to a recording that is still being captured or processed.
 *
 * The state lives here rather than in the panel because the two halves of this
 * feature ended up in different places: the upload action belongs on the
 * capture toolbar, where it is found, and the list belongs in a card, which
 * only exists once there is something to list.
 *
 * Polls only while something is mid-parse, and swallows a failed poll: the
 * capture itself is unaffected and the next tick usually recovers.
 */
export function useLiveDocuments(recordingId: RecordingId) {
  const [documents, setDocuments] = useState<Document[]>([]);

  const refresh = useCallback(async () => {
    try {
      setDocuments(await getDocuments(recordingId));
    } catch (error) {
      console.error("Failed to load documents", error);
    }
  }, [recordingId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    const pending = documents.some(
      (doc) => doc.status === "PENDING" || doc.status === "PROCESSING",
    );
    if (!pending) return;
    const interval = setInterval(() => void refresh(), POLL_INTERVAL_MS);
    return () => clearInterval(interval);
  }, [documents, refresh]);

  return { documents, refresh };
}
