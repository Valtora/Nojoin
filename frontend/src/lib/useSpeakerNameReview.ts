"use client";

import { useState } from "react";

import { confirmSpeakerName, dismissSpeakerName } from "@/lib/api";
import { getErrorMessage } from "@/lib/errors";
import { libraryVoiceprintNotices } from "@/lib/libraryVoiceprint";
import { useNotificationStore } from "@/lib/notificationStore";
import type { SpeakerNameDecision } from "@/lib/speakerNameReview";
import type { RecordingId, RecordingSpeaker } from "@/types";

/** A speaker whose suggested name is waiting for a decision. */
export interface PendingSpeakerName {
  /**
   * The diarization labels the decision is sent for: one for a transcript
   * line, every pending member of a grouped panel row.
   */
  labels: readonly string[];
  /** The suggested name, as the view shows it. */
  name: string;
}

export interface UseSpeakerNameReviewOptions {
  recordingId: RecordingId;
  onRefresh: () => void;
}

export interface SpeakerNameReview {
  /** A confirm or dismiss is in flight. */
  isReviewing: boolean;
  review: (pending: PendingSpeakerName, decision: SpeakerNameDecision) => Promise<void>;
  /** Confirms each in turn, with one notification for them all. */
  confirmAll: (pending: readonly PendingSpeakerName[]) => Promise<void>;
}

const describeNameCount = (count: number) => (count === 1 ? "1 name" : `${count} names`);

/**
 * Confirm and dismiss for suggested speaker names, shared by the speakers
 * panel and the transcript, with their notifications and the refresh after.
 *
 * A dismissal edits no text here: the server puts back "Speaker N" and
 * carries it into the notes, whole words only, as a rename does.
 */
export function useSpeakerNameReview({
  recordingId,
  onRefresh,
}: UseSpeakerNameReviewOptions): SpeakerNameReview {
  const { addNotification } = useNotificationStore();
  const [isReviewing, setIsReviewing] = useState(false);

  const notifyVoiceprints = (speakers: RecordingSpeaker[]) => {
    for (const message of libraryVoiceprintNotices(speakers)) {
      addNotification({ type: "info", message });
    }
  };

  /** Sends the decision for every label in turn; the first failure stops it. */
  const decide = async (pending: PendingSpeakerName, decision: SpeakerNameDecision) => {
    const send = decision === "confirm" ? confirmSpeakerName : dismissSpeakerName;
    const speakers: RecordingSpeaker[] = [];
    for (const label of pending.labels) {
      speakers.push(...(await send(recordingId, label)));
    }
    return speakers;
  };

  const review = async (pending: PendingSpeakerName, decision: SpeakerNameDecision) => {
    if (isReviewing) return;
    setIsReviewing(true);
    try {
      const speakers = await decide(pending, decision);
      addNotification({
        type: "success",
        message:
          decision === "confirm"
            ? `Confirmed ${pending.name}.`
            : `Removed the name ${pending.name}.`,
      });
      notifyVoiceprints(speakers);
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
        confirmed.push(...(await decide(name, "confirm")));
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
