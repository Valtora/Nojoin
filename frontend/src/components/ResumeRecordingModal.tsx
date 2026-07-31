"use client";

import { PauseCircle, Square } from "lucide-react";

import type { Recording } from "@/types";
import Button from "./ui/Button";
import Modal from "./ui/Modal";

interface ResumeRecordingModalProps {
  isOpen: boolean;
  recording: Recording | null;
  busyAction?: "resume" | "cancel" | "stop" | null;
  onResume: () => void;
  onCancel: () => void;
  onStop: () => void;
}

export default function ResumeRecordingModal({
  isOpen,
  recording,
  busyAction = null,
  onResume,
  onCancel,
  onStop,
}: ResumeRecordingModalProps) {
  if (!recording) {
    return null;
  }

  return (
    <Modal
      open={isOpen}
      // The paused recording holds the capture lock, so there is no neutral way
      // out: the modal has to be resolved by one of its three actions.
      onClose={() => {}}
      dismissible={false}
      hideCloseButton
      size="lg"
      footer={
        <>
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
            disabled={busyAction !== null}
            loading={busyAction === "resume"}
          >
            Resume recording
          </Button>
        </>
      }
    >
      <div className="flex items-start gap-3">
        <div className="rounded-lg bg-action-tint p-2 text-action-tint-fg">
          <PauseCircle aria-hidden="true" className="h-5 w-5" />
        </div>
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.22em] text-action-text">
            Recording paused
          </p>
          <h2 className="mt-2 text-xl font-semibold text-foreground">
            Choose what to do before starting anything new
          </h2>
        </div>
      </div>

      <p className="mt-4 text-sm leading-6 text-contrast-helper">
        Nojoin found a paused recording for your account. Resume it to keep
        recording, stop it to process the audio captured so far, or discard it
        to clear the capture lock.
      </p>

      <div className="mt-4 rounded-lg border border-action-border bg-action-tint px-4 py-3 text-sm text-action-tint-fg">
        <p className="font-medium">{recording.name}</p>
        <p className="mt-1 opacity-80">Recording ID: {recording.id}</p>
      </div>
    </Modal>
  );
}
