"use client";

import { useCallback, useState } from "react";

import { useCapture } from "@/lib/capture/CaptureProvider";
import type {
  CaptureDisplaySurface,
  CaptureMode,
  CaptureSourceChoice,
} from "@/lib/capture/shared";
import { RECORDING_UPDATED_EVENT } from "@/lib/recordingEvents";

import Button from "./ui/Button";
import Modal from "./ui/Modal";
import { useResumeRecoveryOffer } from "./useResumeRecoveryOffer";
import { useStartMeeting } from "./useStartMeeting";

interface SourceChoiceCopy {
  title: string;
  paragraphs: string[];
  /** The button that records system audio, and the one that records the microphone only. */
  shareLabel: string;
  microphoneLabel: string;
  /** Which of the two is the primary action, focused when the dialog opens. */
  primary: CaptureMode;
}

const STAYS_PAUSED = "The recording stays paused until you choose.";
const NOT_SHARED =
  "You didn't share a tab, window or screen, so Nojoin can't record the meeting's system audio.";

const NO_SOUND: Record<CaptureDisplaySurface | "unknown", string> = {
  browser:
    "Nojoin can see the tab you shared but can't hear it. Share it again and leave Also share tab audio on.",
  window:
    "Your browser can't share the sound of a single window. Share the meeting's tab instead, or your entire screen with Also share system audio on.",
  monitor:
    "Nojoin can see your screen but can't hear it. Share it again and turn on Also share system audio. On a Mac, if the picker has no audio option, share the meeting's tab instead.",
  unknown:
    "Nojoin can see what you shared but can't hear it. Share it again and turn on the picker's audio option.",
};

/** The dialog's words for each question, at Start and at Resume. */
export function sourceChoiceCopy(choice: CaptureSourceChoice): SourceChoiceCopy {
  const resuming = choice.action === "resume";
  const microphoneOnly = resuming ? "Resume microphone only" : "Record microphone only";
  switch (choice.reason) {
    case "ask":
      return {
        title: "Choose what to record",
        paragraphs: resuming
          ? [
              "Resume with the meeting's system audio and your microphone, or with your microphone only.",
              STAYS_PAUSED,
            ]
          : [
              "Record the meeting's system audio and your microphone, or only your microphone for a meeting in the room or a call on speaker.",
            ],
        shareLabel: "Microphone and system audio",
        microphoneLabel: "Microphone only",
        primary: "shared_audio",
      };
    case "cancelled":
      return {
        title: resuming ? "Resume with your microphone only?" : "Record your microphone only?",
        paragraphs: resuming
          ? [NOT_SHARED, STAYS_PAUSED]
          : [
              NOT_SHARED,
              "For a meeting in the room, or a call on speaker, your microphone records everyone it can hear.",
            ],
        shareLabel: "Share system audio",
        microphoneLabel: microphoneOnly,
        primary: "microphone_only",
      };
    case "refused":
      return {
        title: "Your browser didn't share system audio",
        paragraphs: [
          "The browser or your computer refused to share the screen. On a Mac, allow screen recording for your browser in System Settings > Privacy & Security, then try again.",
          resuming ? "Or resume with your microphone only." : "Or record your microphone only.",
        ],
        shareLabel: "Share system audio",
        microphoneLabel: microphoneOnly,
        primary: "microphone_only",
      };
    case "no_audio":
      return {
        title: "Your share has no sound",
        paragraphs: [
          NO_SOUND[choice.displaySurface ?? "unknown"],
          ...(resuming ? [STAYS_PAUSED] : []),
        ],
        shareLabel: "Share again",
        microphoneLabel: microphoneOnly,
        primary: "shared_audio",
      };
  }
}

const announceStarted = () => window.dispatchEvent(new Event(RECORDING_UPDATED_EVENT));

/**
 * Asks which source to record from (issue #958): under Ask each time before
 * the share picker opens, and after a share that was cancelled, refused or
 * came back without sound. Each choice is its own click, so a share opened
 * from here has the user activation the browser's picker needs.
 */
export default function CaptureSourceDialog() {
  const { dismissSourceChoice, sourceChoice } = useCapture();
  const startMeeting = useStartMeeting(announceStarted);
  const resumeOffer = useResumeRecoveryOffer();
  // Kept while the dialog animates closed, so its words do not vanish first.
  const [shown, setShown] = useState<CaptureSourceChoice | null>(sourceChoice);
  if (sourceChoice && sourceChoice !== shown) {
    setShown(sourceChoice);
  }

  const choose = useCallback((mode: CaptureMode) => {
    if (!sourceChoice) return;
    dismissSourceChoice();
    if (sourceChoice.action === "resume" && sourceChoice.recordingId) {
      void resumeOffer(sourceChoice.recordingId, mode);
    } else {
      void startMeeting(mode);
    }
  }, [dismissSourceChoice, resumeOffer, sourceChoice, startMeeting]);

  if (!shown) return null;
  const copy = sourceChoiceCopy(shown);
  const buttons: Record<CaptureMode, { label: string; mode: CaptureMode }> = {
    shared_audio: { label: copy.shareLabel, mode: "shared_audio" },
    microphone_only: { label: copy.microphoneLabel, mode: "microphone_only" },
  };
  const secondary = buttons[copy.primary === "shared_audio" ? "microphone_only" : "shared_audio"];
  const primary = buttons[copy.primary];

  return (
    <Modal
      open={sourceChoice !== null}
      onClose={dismissSourceChoice}
      title={copy.title}
      size="lg"
      hideCloseButton
      footer={
        <>
          <Button variant="ghost" onClick={dismissSourceChoice}>
            Cancel
          </Button>
          <Button variant="secondary" onClick={() => choose(secondary.mode)}>
            {secondary.label}
          </Button>
          <Button variant="primary" data-autofocus onClick={() => choose(primary.mode)}>
            {primary.label}
          </Button>
        </>
      }
    >
      <div className="space-y-2 text-sm leading-6 text-contrast-helper">
        {copy.paragraphs.map((paragraph) => (
          <p key={paragraph}>{paragraph}</p>
        ))}
      </div>
    </Modal>
  );
}
