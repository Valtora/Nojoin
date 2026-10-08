import { beforeEach, describe, expect, it } from "vitest";
import { renderHook } from "@testing-library/react";

import { useNotificationStore } from "@/lib/notificationStore";
import { RecordingStatus, type Recording } from "@/types";

import { useRecordingStatusNotifications } from "./useRecordingStatusNotifications";

const OOM_MESSAGE =
  "Transcription failed: the GPU ran out of memory (CUDA out of memory) while running parakeet.";

const recording = (
  status: RecordingStatus,
  transcript?: Partial<Recording["transcript"]>,
) =>
  ({
    id: "rec-1",
    name: "Weekly sync",
    status,
    transcript: transcript ? { segments: [], ...transcript } : undefined,
  }) as unknown as Recording;

const errorToasts = () =>
  useNotificationStore
    .getState()
    .activeNotifications.filter((n) => n.type === "error")
    .map((n) => n.message);

const watch = (initial: Recording) =>
  renderHook(({ recordings }) => useRecordingStatusNotifications(recordings), {
    initialProps: { recordings: [initial] },
  });

describe("useRecordingStatusNotifications transcription failures", () => {
  beforeEach(() => {
    useNotificationStore.setState({ activeNotifications: [], history: [] });
  });

  it("raises an error toast when a running transcription fails", () => {
    const { rerender } = watch(
      recording(RecordingStatus.PROCESSING, { transcript_status: "processing" }),
    );

    rerender({
      recordings: [
        recording(RecordingStatus.ERROR, {
          transcript_status: "error",
          error_message: OOM_MESSAGE,
        }),
      ],
    });

    expect(errorToasts()).toEqual([`"Weekly sync": ${OOM_MESSAGE}`]);
  });

  it("raises it for a retry, whose failed transcript row is new", () => {
    // Reprocessing deletes the transcript, so the failure arrives on a fresh row.
    const { rerender } = watch(recording(RecordingStatus.QUEUED));

    rerender({
      recordings: [
        recording(RecordingStatus.ERROR, {
          transcript_status: "error",
          error_message: OOM_MESSAGE,
        }),
      ],
    });

    expect(errorToasts()).toEqual([`"Weekly sync": ${OOM_MESSAGE}`]);
  });

  it("stays quiet about a failure that was already there on load", () => {
    const failed = recording(RecordingStatus.ERROR, {
      transcript_status: "error",
      error_message: OOM_MESSAGE,
    });
    const { rerender } = watch(failed);

    rerender({ recordings: [{ ...failed }] });

    expect(errorToasts()).toEqual([]);
  });
});
