"use client";

import { useCallback } from "react";
import { useRouter } from "next/navigation";

import { useCapture } from "@/lib/capture/CaptureProvider";
import type { CaptureMode } from "@/lib/capture/shared";
import { getErrorMessage } from "@/lib/errors";
import { useNotificationStore } from "@/lib/notificationStore";
import type { RecordingId } from "@/types";

/**
 * Resumes a recording offered for recovery and opens its page: the recovery
 * dialog's Resume, and the recording header's for a tab reloaded during the
 * capture. No runtime is needed; the resume shares again and, after a reload,
 * reclaims the capture with the epoch the tab stored. `mode` is the source
 * picked in the capture dialog; without it the meeting's own source is used,
 * and the page opens only once the recording runs again.
 */
export function useResumeRecoveryOffer() {
  const router = useRouter();
  const { controller, resume } = useCapture();
  const { addNotification } = useNotificationStore();

  return useCallback(async (recordingId: RecordingId, mode?: CaptureMode) => {
    try {
      const resumed = await (mode ? resume(recordingId, { mode }) : resume(recordingId));
      if (resumed) router.push(`/recordings/${recordingId}`);
    } catch (resumeError: unknown) {
      if (!controller.getState().error) {
        addNotification({
          type: "error",
          message: getErrorMessage(resumeError, "Failed to resume the paused recording."),
        });
      }
    }
  }, [addNotification, controller, resume, router]);
}
