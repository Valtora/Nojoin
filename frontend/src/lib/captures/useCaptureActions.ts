"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { useCapture } from "@/lib/capture/CaptureProvider";
import { getErrorMessage } from "@/lib/errors";
import { formatTimelineMs } from "@/lib/notes/placement";
import type { RecordingId } from "@/types";

import { grabDisplayFrame } from "./frameImage";

/** The keys that take a capture, as the button's tooltip and the guide name them. */
export const CAPTURE_SHORTCUT_LABEL = "Alt+Shift+C";
export const CAPTURE_SHORTCUT_LABEL_MAC = "Option+Shift+C";

/** The text of the note a capture makes, which the user can edit into a caption. */
export const CAPTURE_NOTE_BODY = "Screen capture";

/** Whether a key press is the capture shortcut, by key position, whatever the layout. */
export const isCaptureShortcut = (event: Pick<KeyboardEvent, "code" | "altKey" | "shiftKey" | "ctrlKey" | "metaKey">) =>
  event.code === "KeyC" && event.altKey && event.shiftKey && !event.ctrlKey && !event.metaKey;

export const captureShortcutLabel = (): string =>
  typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform ?? "")
    ? CAPTURE_SHORTCUT_LABEL_MAC
    : CAPTURE_SHORTCUT_LABEL;

export interface CaptureActions {
  /** Whether this tab holds the shared screen, so the Capture button is offered. */
  canCaptureScreen: boolean;
  /** This desktop tab owns the recording, but has no shared picture to capture. */
  screenCaptureHint: string | null;
  /** A capture is being taken. */
  busy: boolean;
  /** Takes a still of the shared screen at the recording's time now. */
  captureScreen: () => Promise<void>;
  /** What the last attempt said, for the note box's status line. */
  message: { text: string; tone: "info" | "error"; seq: number } | null;
}

/**
 * Taking a still of the shared screen during a recording, from the Capture
 * button or Alt+Shift+C (Option+Shift+C on a Mac). The time is read when the
 * user asks, before the frame is drawn, so it is where the meeting was then.
 * `onFrame` makes the point note that holds it, and says why it could not.
 * The shortcut is listened for on the whole page while this tab holds the
 * share; the browser sends it only while Nojoin's tab has focus.
 */
export function useCaptureActions(
  recordingId: RecordingId,
  readAudioNow: () => number,
  onFrame: (file: File, atMs: number) => string | null,
): CaptureActions {
  const { runtimeActive, recordingId: activeRecordingId, displayVideoTrack, support } = useCapture();
  const canCaptureScreen =
    runtimeActive && typeof displayVideoTrack === "function"
      ? displayVideoTrack(recordingId) !== null
      : false;
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);
  const [message, setMessage] = useState<CaptureActions["message"]>(null);
  const say = useCallback(
    (text: string, tone: "info" | "error") =>
      setMessage((previous) => ({ text, tone, seq: (previous?.seq ?? 0) + 1 })),
    [],
  );

  const captureScreen = useCallback(async () => {
    const track = displayVideoTrack?.(recordingId) ?? null;
    if (!track || busyRef.current) return;
    const atMs = readAudioNow();
    busyRef.current = true;
    setBusy(true);
    try {
      const frame = await grabDisplayFrame(track);
      const extension = frame.type === "image/jpeg" ? "jpg" : "png";
      const file = new File([frame], `screen-capture.${extension}`, { type: frame.type });
      const refusal = onFrame(file, atMs);
      say(refusal ?? `Screen captured at ${formatTimelineMs(atMs)}.`, refusal ? "error" : "info");
    } catch (error: unknown) {
      say(getErrorMessage(error, "The shared screen could not be captured."), "error");
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  }, [displayVideoTrack, recordingId, readAudioNow, onFrame, say]);

  useEffect(() => {
    if (!canCaptureScreen) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (!isCaptureShortcut(event) || event.repeat) return;
      // Option+Shift+C types "Ç" on a Mac; the capture replaces it.
      event.preventDefault();
      void captureScreen();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [canCaptureScreen, captureScreen]);

  // Only a browser that can share a tab or screen gets the hint.
  const screenCaptureHint = runtimeActive && activeRecordingId === recordingId && !canCaptureScreen
    && support.supported && support.mode === "shared_audio"
    ? "Screen captures need a shared tab, window or screen."
    : null;
  return { canCaptureScreen, screenCaptureHint, busy, captureScreen, message };
}
