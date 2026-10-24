"use client";

import { useState } from "react";

import { confirmSpeakerName, dismissSpeakerName } from "@/lib/api";
import { getErrorMessage } from "@/lib/errors";
import { libraryVoiceprintNotices } from "@/lib/libraryVoiceprint";
import { useNotificationStore } from "@/lib/notificationStore";
import { getRecordingSpeakerDisplayName } from "@/lib/recordingSpeakerUtils";
import type { SpeakerNameDecision } from "@/lib/speakerNameReview";
import type { GlobalSpeaker, RecordingId, RecordingSpeaker } from "@/types";

/** A speaker whose suggested name is waiting for a decision. */
export interface PendingSpeakerName {
  /** The diarization label the decision is sent for. */
  label: string;
  /** The suggested name, as the view shows it. */
  name: string;
}

export interface UseSpeakerNameReviewOptions {
  recordingId: RecordingId;
  onRefresh: () => void;
  /** Carries a name change into the notes and title, as a rename does. */
  onSpeakerRenamed?: (oldName: string, newName: string) => Promise<void> | void;
}

export interface SpeakerNameReview {
  /** A confirm or dismiss is in flight. */
  isReviewing: boolean;
  review: (pending: PendingSpeakerName, decision: SpeakerNameDecision) => Promise<void>;
  /** Confirms each in turn, with one notification for them all. */
  confirmAll: (pending: readonly PendingSpeakerName[]) => Promise<void>;
}

// The response's speaker is named locally once its suggestion is dropped, so
// no library lookup is needed to read its name.
const NO_GLOBAL_SPEAKERS = new Map<number, GlobalSpeaker>();

const nameAfter = (speakers: RecordingSpeaker[], pending: PendingSpeakerName) => {
  const speaker = speakers.find((candidate) => candidate.diarization_label === pending.label);
  return speaker ? getRecordingSpeakerDisplayName(speaker, NO_GLOBAL_SPEAKERS) : pending.label;
};

const describeNameCount = (count: number) => (count === 1 ? "1 name" : `${count} names`);

/**
 * Confirm and dismiss for suggested speaker names, shared by the speakers
 * panel and the transcript, with their notifications and the refresh after.
 */
export function useSpeakerNameReview({
  recordingId,
  onRefresh,
  onSpeakerRenamed,
}: UseSpeakerNameReviewOptions): SpeakerNameReview {
  const { addNotification } = useNotificationStore();
  const [isReviewing, setIsReviewing] = useState(false);

  const notifyVoiceprints = (speakers: RecordingSpeaker[]) => {
    for (const message of libraryVoiceprintNotices(speakers)) {
      addNotification({ type: "info", message });
    }
  };

  const review = async (pending: PendingSpeakerName, decision: SpeakerNameDecision) => {
    if (isReviewing) return;
    setIsReviewing(true);
    try {
      if (decision === "confirm") {
        const speakers = await confirmSpeakerName(recordingId, pending.label);
        addNotification({ type: "success", message: `Confirmed ${pending.name}.` });
        notifyVoiceprints(speakers);
      } else {
        const speakers = await dismissSpeakerName(recordingId, pending.label);
        const newName = nameAfter(speakers, pending);
        if (newName !== pending.name) {
          await onSpeakerRenamed?.(pending.name, newName);
        }
        addNotification({ type: "success", message: `Removed the name ${pending.name}.` });
        notifyVoiceprints(speakers);
      }
    } catch (e: unknown) {
      console.error(`Failed to ${decision} the speaker name`, e);
      addNotification({
        type: "error",
        message: getErrorMessage(
          e,
          decision === "confirm"
            ? `Failed to confirm ${pending.name}.`
            : `Failed to remove the name ${pending.name}.`,
        ),
      });
    } finally {
      setIsReviewing(false);
      // After a failure too: a 409 means the name was settled elsewhere, and
      // the view should catch up with that.
      onRefresh();
    }
  };

  const confirmAll = async (pending: readonly PendingSpeakerName[]) => {
    if (isReviewing || pending.length === 0) return;
    setIsReviewing(true);
    const confirmed: RecordingSpeaker[] = [];
    let failures = 0;
    for (const name of pending) {
      try {
        confirmed.push(...(await confirmSpeakerName(recordingId, name.label)));
      } catch (e: unknown) {
        console.error(`Failed to confirm the speaker name ${name.name}`, e);
        failures += 1;
      }
    }
    setIsReviewing(false);
    onRefresh();
    const done = pending.length - failures;
    addNotification(
      failures === 0
        ? { type: "success", message: `Confirmed ${describeNameCount(done)}.` }
        : {
            type: "error",
            message: `Confirmed ${done} of ${describeNameCount(pending.length)}. Try the others again.`,
          },
    );
    notifyVoiceprints(confirmed);
  };

  return { isReviewing, review, confirmAll };
}
