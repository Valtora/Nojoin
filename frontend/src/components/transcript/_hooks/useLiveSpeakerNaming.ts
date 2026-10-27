"use client";

import { useCallback, useRef } from "react";

import { getGlobalSpeakers, nameLiveSpeaker } from "@/lib/api/speakers";
import { getErrorDetail } from "@/lib/errors";
import { useNotificationStore } from "@/lib/notificationStore";
import type { RecordingId, TranscriptSegment } from "@/types";
import { sourceReference } from "@/lib/transcript/settling";

export interface LiveSpeakerNaming {
  /** Names live speaker `label`; a failure is reported, never thrown. */
  renameLiveSpeaker: (label: string, name: string, segment?: TranscriptSegment) => Promise<void>;
  /** People's names, read once on the first rename and kept. */
  loadPeopleNames: () => Promise<readonly string[]>;
}

/**
 * Naming a live speaker from the live panel during capture.
 *
 * The answer carries every live name, which `applyNames` shows at once rather
 * than waiting for the next read of the live transcript.
 */
export function useLiveSpeakerNaming(
  recordingId: RecordingId,
  applyNames: (names: Record<string, string>) => void,
): LiveSpeakerNaming {
  const { addNotification } = useNotificationStore();
  const peopleNames = useRef<Promise<readonly string[]> | null>(null);

  const renameLiveSpeaker = useCallback(
    async (label: string, name: string, segment?: TranscriptSegment) => {
      try {
        const anchor = segment ? sourceReference(segment) : null;
        applyNames(await (anchor
          ? nameLiveSpeaker(recordingId, label, name, anchor)
          : nameLiveSpeaker(recordingId, label, name)));
      } catch (error) {
        console.error("Failed to name the live speaker", error);
        addNotification({
          type: "error",
          // The server says why, as when the meeting has just been stopped.
          message: getErrorDetail(error) ?? "Failed to rename the speaker.",
        });
      }
    },
    [recordingId, applyNames, addNotification],
  );

  const loadPeopleNames = useCallback(() => {
    peopleNames.current ??= getGlobalSpeakers().then(
      (people) => people.map((person) => person.name),
      () => {
        // Try again on the next rename.
        peopleNames.current = null;
        return [];
      },
    );
    return peopleNames.current;
  }, []);

  return { renameLiveSpeaker, loadPeopleNames };
}
