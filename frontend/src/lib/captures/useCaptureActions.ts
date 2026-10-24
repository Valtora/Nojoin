"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { useCapture } from "@/lib/capture/CaptureProvider";
import { getErrorMessage } from "@/lib/errors";
import { formatTimelineMs } from "@/lib/notes/placement";
import type { RecordingId } from "@/types";

import { captureOutbox } from "./captureOutbox";
import { grabDisplayFrame, imageForCapture } from "./frameImage";

/** The keys that take a capture, as the button's tooltip and the guide name them. */
export const CAPTURE_SHORTCUT_LABEL = "Alt+Shift+C";
export const CAPTURE_SHORTCUT_LABEL_MAC = "Option+Shift+C";

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
  /** A capture is being taken. */
  busy: boolean;
  /** Takes a still of the shared screen at the recording's time now. */
  captureScreen: () => Promise<void>;
  /** Stores a pasted image as a capture at the recording's time now. */
  addImage: (image: Blob) => Promise<void>;
  /** What the last attempt said, for the note box's status line. */
  message: { text: string; tone: "info" | "error"; seq: number } | null;
}

/**
 * Taking captures during a recording: a still of the shared screen, from the
 * Capture button or Alt+Shift+C (Option+Shift+C on a Mac), or an image pasted
 * into the note box. The time is read when the user asks, before the frame is
 * drawn, so it is where the meeting was then. The shortcut is listened for on
 * the whole page while this tab holds the share; the browser sends it only
 * while Nojoin's tab has focus.
 */
export function useCaptureActions(
  recordingId: RecordingId,
  readAudioNow: () => number,
): CaptureActions {
  const { runtimeActive, displayVideoTrack } = useCapture();
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

  const store = useCallback(
    (image: Blob, atMs: number) => {
      captureOutbox.add(recordingId, image, atMs);
      say(`Capture taken at ${formatTimelineMs(atMs)}.`, "info");
    },
    [recordingId, say],
  );

  const captureScreen = useCallback(async () => {
    const track = displayVideoTrack?.(recordingId) ?? null;
    if (!track || busyRef.current) return;
    const atMs = readAudioNow();
    busyRef.current = true;
    setBusy(true);
    try {
      store(await grabDisplayFrame(track), atMs);
    } catch (error: unknown) {
      say(getErrorMessage(error, "The shared screen could not be captured."), "error");
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  }, [displayVideoTrack, recordingId, readAudioNow, store, say]);

  const addImage = useCallback(
    async (image: Blob) => {
      const atMs = readAudioNow();
      try {
        store(await imageForCapture(image), atMs);
      } catch (error: unknown) {
        say(getErrorMessage(error, "The pasted image could not be read."), "error");
      }
    },
    [readAudioNow, store, say],
  );

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

  return { canCaptureScreen, busy, captureScreen, addImage, message };
}
