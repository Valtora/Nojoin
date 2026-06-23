"use client";

import { useEffect, useRef } from "react";

import { Recording, RecordingId, RecordingStatus } from "@/types";
import { useNotificationStore } from "@/lib/notificationStore";

/**
 * Watches a recordings list for status transitions and raises the matching
 * toast notifications (processing complete, notes generated/failed, transcript
 * ready). Extracted verbatim from {@link Sidebar} (FE-012); the transition
 * tracking refs and emitted messages are unchanged.
 */
export function useRecordingStatusNotifications(
  recordings: Recording[],
): void {
  const { addNotification } = useNotificationStore();
  const prevRecordingsRef = useRef<Map<RecordingId, RecordingStatus>>(new Map());
  const prevNotesStatusRef = useRef<Map<RecordingId, string>>(new Map());
  const prevTranscriptStatusRef = useRef<Map<RecordingId, string>>(new Map());

  useEffect(() => {
    recordings.forEach((rec) => {
      // Check Recording Status (General Processing)
      const prevStatus = prevRecordingsRef.current.get(rec.id);
      if (
        prevStatus &&
        prevStatus !== RecordingStatus.PROCESSED &&
        rec.status === RecordingStatus.PROCESSED
      ) {
        addNotification({
          type: "success",
          message: `Processing completed for "${rec.name}"`,
        });
      }
      prevRecordingsRef.current.set(rec.id, rec.status);

      // Check Notes Status (Specific)
      if (rec.transcript) {
        const prevNotesStatus = prevNotesStatusRef.current.get(rec.id);
        const currentNotesStatus = rec.transcript.notes_status;

        if (
          prevNotesStatus &&
          prevNotesStatus !== "completed" &&
          currentNotesStatus === "completed"
        ) {
          addNotification({
            type: "success",
            message: `Meeting notes generated for "${rec.name}"`,
          });
        }
        if (
          prevNotesStatus &&
          prevNotesStatus !== "error" &&
          currentNotesStatus === "error"
        ) {
          addNotification({
            type: "error",
            message: rec.transcript.error_message
              ? `Meeting notes failed for "${rec.name}": ${rec.transcript.error_message}`
              : `Meeting notes failed for "${rec.name}"`,
          });
        }
        prevNotesStatusRef.current.set(rec.id, currentNotesStatus || "pending");

        // Check Transcript Status (Specific)
        const prevTranscriptStatus = prevTranscriptStatusRef.current.get(rec.id);
        const currentTranscriptStatus = rec.transcript.transcript_status;

        if (
          prevTranscriptStatus &&
          prevTranscriptStatus !== "completed" &&
          currentTranscriptStatus === "completed"
        ) {
          addNotification({
            type: "success",
            message: `Transcript ready for "${rec.name}"`,
          });
        }
        prevTranscriptStatusRef.current.set(
          rec.id,
          currentTranscriptStatus || "pending",
        );
      }
    });
  }, [recordings, addNotification]);
}
