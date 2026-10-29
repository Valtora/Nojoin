"use client";

import { AlertTriangle, PauseCircle, Square } from "lucide-react";

import type { CaptureSupport } from "@/lib/capture/shared";
import { RecordingStatus, type Recording } from "@/types";
import CaptureUnsupportedNotice from "./CaptureUnsupportedNotice";
import Button from "./ui/Button";
import Modal from "./ui/Modal";

interface ResumeRecordingModalProps {
  isOpen: boolean;
  recording: Recording | null;
  busyAction?: "resume" | "cancel" | "stop" | null;
  /** This browser's capture support: Resume needs it, Stop and Discard do not. */
  support: CaptureSupport;
  onResume: () => void;
  onCancel: () => void;
  onStop: () => void;
  /** Closes the dialog and leaves the recording as it is. */
  onDismiss: () => void;
}

export default function ResumeRecordingModal({
  isOpen,
  recording,
  busyAction = null,
  support,
  onResume,
  onCancel,
  onStop,
  onDismiss,
}: ResumeRecordingModalProps) {
  if (!recording) {
    return null;
  }

  // Still marked as uploading once the server judged it stalled: its tab
  // closed without the pause arriving (issue #519). A reloaded tab is offered
  // its own capture before that (issue #778), and reads as paused.
  const interrupted = recording.status === RecordingStatus.UPLOADING && Boolean(recording.capture_stalled);
  const StatusIcon = interrupted ? AlertTriangle : PauseCircle;

  return (
    <Modal
      open={isOpen}
      // Leaving it changes nothing: the recording keeps its badge, and opening
      // it or starting a meeting offers this dialog again. Only Not now closes
      // it, so a stray click outside cannot.
      onClose={onDismiss}
      dismissible={false}
      hideCloseButton
      size="lg"
      footer={
        <>
          <Button variant="ghost" onClick={onDismiss} disabled={busyAction !== null}>
            Not now
          </Button>
          <Button
            variant="danger"
            onClick={onCancel}
            disabled={busyAction !== null}
            loading={busyAction === "cancel"}
          >
            Discard recording
          </Button>
          {/*
            Stopping from here finalizes the uploaded segments without
            re-acquiring the share picker. Without it, a recording whose browser
            runtime was torn down could only be resumed or destroyed (issue #166).
          */}
          <Button
            variant="secondary"
            onClick={onStop}
            disabled={busyAction !== null}
            loading={busyAction === "stop"}
            iconLeft={<Square aria-hidden="true" className="h-4 w-4 fill-current" />}
          >
            Stop and process
          </Button>
          <Button
            variant="primary"
            onClick={onResume}
            disabled={busyAction !== null || !support.supported}
            loading={busyAction === "resume"}
          >
            Resume recording
          </Button>
        </>
      }
    >
      <div className="flex items-start gap-3">
        <div className="rounded-lg bg-action-tint p-2 text-action-tint-fg">
          <StatusIcon aria-hidden="true" className="h-5 w-5" />
        </div>
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.22em] text-action-text">
            {interrupted ? "Recording interrupted" : "Recording paused"}
          </p>
          <h2 className="mt-2 text-xl font-semibold text-foreground">
            Choose what to do before starting anything new
          </h2>
        </div>
      </div>

      {interrupted ? (
        <p className="mt-4 text-sm leading-6 text-contrast-helper">
          Nojoin has received no audio from this recording for over two minutes,
          so the tab recording it was probably closed or cut off. If it is still
          open on another device, finish it there. Otherwise resume it to keep
          recording, stop it to process the audio captured so far, or discard it.
        </p>
      ) : (
        <p className="mt-4 text-sm leading-6 text-contrast-helper">
          Nojoin found a paused recording for your account. It may still be open
          on another device; if so, finish it there. Otherwise resume it to keep
          recording, stop it to process the audio captured so far, or discard it
          to clear the capture lock.
        </p>
      )}

      <div className="mt-4 rounded-lg border border-action-border bg-action-tint px-4 py-3 text-sm text-action-tint-fg">
        <p className="font-medium">{recording.name}</p>
        <p className="mt-1 opacity-80">Recording ID: {recording.id}</p>
      </div>

      {support.supported ? null : (
        <div className="mt-4">
          <CaptureUnsupportedNotice reason={support.reason} compact />
        </div>
      )}
    </Modal>
  );
}
