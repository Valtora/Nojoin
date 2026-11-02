"use client";

import { useState } from "react";
import { usePathname, useRouter } from "next/navigation";

import { useCapture } from "@/lib/capture/CaptureProvider";
import { usePausedRecordingGuard } from "@/lib/capture/usePausedRecordingGuard";
import { getErrorMessage } from "@/lib/errors";
import { useNotificationStore } from "@/lib/notificationStore";

import ResumeRecordingModal from "./ResumeRecordingModal";
import SessionSignInDialog from "./SessionSignInDialog";
import { useResumeRecoveryOffer } from "./useResumeRecoveryOffer";

interface CaptureShellProps {
  children: React.ReactNode;
}

export default function CaptureShell({ children }: CaptureShellProps) {
  const router = useRouter();
  const pathname = usePathname();
  const { pausedRecording, hasPausedRecording } = usePausedRecordingGuard();
  const {
    cancel, controller, dismissRecoveryOffer, runtimeActive, status, stop, support,
  } = useCapture();
  const resumeOffer = useResumeRecoveryOffer();
  const [busyAction, setBusyAction] = useState<
    "resume" | "cancel" | "stop" | null
  >(null);
  const { addNotification } = useNotificationStore();

  // Not over a Resume or Start this tab is already running from elsewhere on
  // the page, such as the recording's own Resume: the choice is made, and the
  // dialog would ask for it again over the share picker (#887). Its own
  // Resume keeps it open, showing the wait.
  const ownCaptureStarting = status === "starting" && busyAction === null;
  const modalOpen = hasPausedRecording && !runtimeActive && !ownCaptureStarting;

  const handleResume = async () => {
    if (!pausedRecording) {
      return;
    }

    setBusyAction("resume");
    try {
      await resumeOffer(pausedRecording.id);
    } finally {
      setBusyAction(null);
    }
  };

  // Finalizes straight from the uploaded segments; no share picker, because the
  // browser runtime for this recording is already gone.
  const handleStop = async () => {
    if (!pausedRecording) {
      return;
    }

    setBusyAction("stop");
    try {
      // Named explicitly: this tab may have no capture of its own, or remember
      // a different one, when the dialog offers another tab's lost capture.
      await stop(pausedRecording.id);
      router.push(`/recordings/${pausedRecording.id}`);

        } catch (stopError: unknown) {
      if (!controller.getState().error) {
        addNotification({
          type: "error",
          message: getErrorMessage(
            stopError,
            "Failed to stop and process the paused recording.",
          ),
        });
      }
    } finally {
      setBusyAction(null);
    }
  };

  const handleCancel = async () => {
    if (!pausedRecording) {
      return;
    }

    setBusyAction("cancel");
    try {
      await cancel(pausedRecording.id);
      router.push("/recordings");

        } catch (cancelError: unknown) {
      // A refusal the controller already reports, such as a capture carried
      // on elsewhere since the dialog opened, is not reported twice.
      if (!controller.getState().error) {
        addNotification({
          type: "error",
          message: getErrorMessage(
            cancelError,
            "Failed to discard the paused recording.",
          ),
        });
      }
    } finally {
      setBusyAction(null);
    }
  };

  return (
    <>
      {children}
      <ResumeRecordingModal
        isOpen={modalOpen}
        recording={pausedRecording}
        onItsPage={pausedRecording !== null && pathname === `/recordings/${pausedRecording.id}`}
        busyAction={busyAction}
        support={support}
        onResume={handleResume}
        onCancel={handleCancel}
        onStop={handleStop}
        onDismiss={dismissRecoveryOffer}
      />
      <SessionSignInDialog />
    </>
  );
}
