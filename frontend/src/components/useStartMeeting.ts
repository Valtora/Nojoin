"use client";

import { useCallback } from "react";
import { useRouter } from "next/navigation";

import { useCapture } from "@/lib/capture/CaptureProvider";
import type { CaptureMode } from "@/lib/capture/shared";
import { useNotificationStore } from "@/lib/notificationStore";

/**
 * Starts a new meeting and opens its page: Start Meeting on every surface, and
 * the capture dialog's choice of source. Resolves once the recording runs, or
 * when nothing started because the user is being asked which source to use.
 */
export function useStartMeeting(onStarted?: () => void) {
  const router = useRouter();
  const { controller, start } = useCapture();
  const { addNotification } = useNotificationStore();

  return useCallback(async (mode?: CaptureMode) => {
    let response;
    try {
      response = await (mode ? start("", { mode }) : start(""));
    } catch (err: unknown) {
      console.error("Failed to start browser recording:", err);
      if (!controller.getState().error) {
        addNotification({
          type: "error",
          message:
            err instanceof Error && err.message
              ? err.message
              : err
                ? String(err)
                : "Failed to start browser recording.",
        });
      }
      return;
    }
    if (response?.recordingId) {
      router.push(`/recordings/${response.recordingId}`);
      onStarted?.();
    }
  }, [addNotification, controller, onStarted, router, start]);
}
