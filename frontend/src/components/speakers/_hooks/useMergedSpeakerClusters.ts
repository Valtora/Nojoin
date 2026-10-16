"use client";

import { useEffect, useState } from "react";

import type { MergedRecordingSpeaker, RecordingId, RecordingSpeaker } from "@/types";
import { getMergedRecordingSpeakers } from "@/lib/api";

/**
 * The clusters processing merged into other speakers, for the undo controls
 * in {@link SpeakerPanel}. Reloaded whenever the speaker list changes, since
 * every speaker mutation goes through a refresh of that list.
 */
export function useMergedSpeakerClusters(
  recordingId: RecordingId,
  speakers: RecordingSpeaker[],
): MergedRecordingSpeaker[] {
  const [clusters, setClusters] = useState<MergedRecordingSpeaker[]>([]);

  useEffect(() => {
    let cancelled = false;
    getMergedRecordingSpeakers(recordingId)
      .then((rows) => {
        if (!cancelled) setClusters(rows);
      })
      .catch((e: unknown) => {
        console.error("Failed to load merged speaker clusters", e);
        if (!cancelled) setClusters([]);
      });
    return () => {
      cancelled = true;
    };
  }, [recordingId, speakers]);

  return clusters;
}
