"use client";

import { useCallback, useEffect, useState } from "react";

import { getRecordings } from "@/lib/api";
import { subscribeRecordingRemoved } from "@/lib/recordingEvents";
import { Recording, RecordingStatus } from "@/types";

/**
 * How many recordings the recents module offers.
 *
 * This is a ceiling rather than a count. The module scrolls inside itself and
 * takes the full height of its column, so how many are actually visible is a
 * property of the window rather than of this number; it exists only so that a
 * library of thousands does not render thousands of rows into a scroll area.
 */
const RECENT_LIMIT = 20;

/** Statuses that mean the pipeline is still working on a recording. */
const IN_FLIGHT: ReadonlySet<RecordingStatus> = new Set([
  RecordingStatus.UPLOADING,
  RecordingStatus.QUEUED,
  RecordingStatus.PROCESSING,
]);

interface DashboardRecordings {
  recent: Recording[];
  processing: Recording[];
  loading: boolean;
}

const sortByNewest = (recordings: Recording[]): Recording[] =>
  [...recordings].sort(
    (left, right) =>
      new Date(right.created_at).getTime() - new Date(left.created_at).getTime(),
  );

/**
 * One fetch, two modules.
 *
 * The recents list and the in-flight list are two views of the same response,
 * so they share a hook rather than each calling the API. `getRecordings` has no
 * limit or sort parameter and returns the whole library, which is accepted
 * here: the recordings rail already fetches exactly this on /recordings, so the
 * payload is one the app serves routinely, and slicing client-side keeps the
 * dashboard free of a backend change.
 *
 * Refreshes on the `recording-updated` event the app already dispatches when a
 * capture ends or a recording changes, rather than adding a poller. A recording
 * that finishes processing therefore leaves the in-flight list on the next
 * event rather than on a timer.
 */
export function useDashboardRecordings(): DashboardRecordings {
  const [recordings, setRecordings] = useState<Recording[]>([]);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    try {
      const all = await getRecordings();
      setRecordings(all.filter((recording) => !recording.is_archived && !recording.is_deleted));
    } catch {
      // A dashboard module that cannot load is hidden rather than shouted
      // about: the recordings page is where an error about recordings belongs.
      setRecordings([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();

    const handleUpdate = () => void load();
    window.addEventListener("recording-updated", handleUpdate);
    // A discard is explicit and irreversible, so the row goes now rather than
    // after the re-fetch a generic update would need.
    const unsubscribeRemoved = subscribeRecordingRemoved((id) => {
      setRecordings((prev) => prev.filter((recording) => recording.id !== id));
    });
    return () => {
      window.removeEventListener("recording-updated", handleUpdate);
      unsubscribeRemoved();
    };
  }, [load]);

  const sorted = sortByNewest(recordings);

  return {
    recent: sorted.slice(0, RECENT_LIMIT),
    processing: sorted.filter((recording) => IN_FLIGHT.has(recording.status)),
    loading,
  };
}
