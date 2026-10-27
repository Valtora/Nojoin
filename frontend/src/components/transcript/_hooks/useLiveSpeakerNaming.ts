"use client";

import { useCallback, useRef } from "react";

import { getGlobalSpeakers, nameLiveSpeaker } from "@/lib/api/speakers";
import { getErrorDetail } from "@/lib/errors";
import { useNotificationStore } from "@/lib/notificationStore";
import type { RecordingId, TranscriptSegment } from "@/types";
import { liveSpeakerSourceReference } from "@/lib/transcript/liveSpeakerSource";

export interface LiveSpeakerNaming {
  /** Names live speaker `label`; a failure is reported, never thrown. */
  renameLiveSpeaker: (label: string, name: string, segment?: TranscriptSegment, createPerson?: boolean) => Promise<boolean>;
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
  onAccepted?: () => Promise<void> | void,
): LiveSpeakerNaming {
  const { addNotification } = useNotificationStore();
  const peopleNames = useRef<Promise<readonly string[]> | null>(null);

  const renameLiveSpeaker = useCallback(
    async (label: string, name: string, segment?: TranscriptSegment, createPerson = false) => {
      try {
        const anchor = segment ? liveSpeakerSourceReference(segment) : null;
        if (createPerson && (!anchor || segment?.locked !== true)) {
          addNotification({ type: "error", message: "Wait for a locked transcript line before adding this speaker to People." });
          return false;
        }
        const names = await (createPerson
          ? nameLiveSpeaker(recordingId, label, name, anchor!, true)
          : anchor
          ? nameLiveSpeaker(recordingId, label, name, anchor)
          : nameLiveSpeaker(recordingId, label, name));
        applyNames(names);
        peopleNames.current = null;
        try { await onAccepted?.(); }
        catch (error) { console.error("Failed to refresh accepted speaker naming", error); }
        return true;
      } catch (error) {
        console.error("Failed to name the live speaker", error);
        addNotification({
          type: "error",
          // The server says why, as when the meeting has just been stopped.
          message: getErrorDetail(error) ?? "Failed to rename the speaker.",
        });
        return false;
      }
    },
    [recordingId, applyNames, addNotification, onAccepted],
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
