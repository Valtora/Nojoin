"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

import { useCapture } from "@/lib/capture/CaptureProvider";
import { usePausedRecordingGuard } from "@/lib/capture/usePausedRecordingGuard";
import { getErrorMessage } from "@/lib/errors";
import { useNotificationStore } from "@/lib/notificationStore";

import ResumeRecordingModal from "./ResumeRecordingModal";

interface CaptureShellProps {
  children: React.ReactNode;
}

export default function CaptureShell({ children }: CaptureShellProps) {
  const router = useRouter();
  const { pausedRecording, hasPausedRecording } = usePausedRecordingGuard();
  const { cancel, controller, resume, runtimeActive } = useCapture();
  const [busyAction, setBusyAction] = useState<"resume" | "cancel" | null>(null);
  const { addNotification } = useNotificationStore();

  const modalOpen = hasPausedRecording && !runtimeActive;

  const handleResume = async () => {
    if (!pausedRecording) {
      return;
    }

    setBusyAction("resume");
    try {
      await resume(pausedRecording.id);
      router.push(`/recordings/${pausedRecording.id}`);

        } catch (resumeError: unknown) {
      if (!controller.getState().error) {
        addNotification({
          type: "error",
          message: getErrorMessage(
            resumeError,
            "Failed to resume the paused recording.",
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
      addNotification({
        type: "error",
        message: getErrorMessage(
          cancelError,
          "Failed to discard the paused recording.",
        ),
      });
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
        busyAction={busyAction}
        onResume={handleResume}
        onCancel={handleCancel}
      />
    </>
  );
}
