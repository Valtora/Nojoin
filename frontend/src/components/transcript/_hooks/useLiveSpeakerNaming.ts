"use client";

import { useCallback, useRef } from "react";

import { getGlobalSpeakers, mergeLiveSpeaker, nameLiveSpeaker } from "@/lib/api/speakers";
import { getErrorDetail } from "@/lib/errors";
import { useNotificationStore } from "@/lib/notificationStore";
import type { RecordingId } from "@/types";

export interface LiveSpeakerNaming {
  /** Names live speaker `label`; a failure is reported, never thrown. */
  renameLiveSpeaker: (label: string, name: string, createPerson?: boolean) => Promise<boolean>;
  /** Merges live speaker `label` into `intoLabel`; a failure is reported, never thrown. */
  mergeLiveSpeaker: (label: string, intoLabel: string) => Promise<boolean>;
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
  applyMerges?: (merges: Record<string, string>) => void,
): LiveSpeakerNaming {
  const { addNotification } = useNotificationStore();
  const peopleNames = useRef<Promise<readonly string[]> | null>(null);

  const renameLiveSpeaker = useCallback(
    async (label: string, name: string, createPerson = false) => {
      try {
        applyNames(await nameLiveSpeaker(recordingId, label, name, createPerson));
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

  const mergeLive = useCallback(
    async (label: string, intoLabel: string) => {
      try {
        const answer = await mergeLiveSpeaker(recordingId, label, intoLabel);
        applyNames(answer.live_speaker_names);
        applyMerges?.(answer.live_speaker_merges ?? {});
        try { await onAccepted?.(); }
        catch (error) { console.error("Failed to refresh after merging live speakers", error); }
        return true;
      } catch (error) {
        console.error("Failed to merge the live speakers", error);
        addNotification({
          type: "error",
          message: getErrorDetail(error) ?? "Failed to merge the speakers.",
        });
        return false;
      }
    },
    [recordingId, applyNames, applyMerges, addNotification, onAccepted],
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

  return { renameLiveSpeaker, mergeLiveSpeaker: mergeLive, loadPeopleNames };
}
