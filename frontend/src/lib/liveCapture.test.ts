import { describe, expect, it } from "vitest";

import { isLiveCaptureInProgress } from "./liveCapture";
import { ClientStatus, Recording, RecordingStatus } from "@/types";

const buildRecording = (overrides: Partial<Recording> = {}): Recording => ({
  id: "rec-1",
  created_at: "2026-07-25T10:00:00Z",
  updated_at: "2026-07-25T10:00:00Z",
  name: "Live meeting",
  meeting_uid: "meeting-1",
  audio_path: "/tmp/audio.wav",
  status: RecordingStatus.UPLOADING,
  client_status: ClientStatus.RECORDING,
  upload_progress: 10,
  processing_progress: 0,
  is_archived: false,
  is_deleted: false,
  ...overrides,
});

describe("isLiveCaptureInProgress", () => {
  it("accepts a running browser capture", () => {
    expect(
      isLiveCaptureInProgress(
        buildRecording({ client_status: ClientStatus.RECORDING }),
      ),
    ).toBe(true);
  });

  it("accepts a paused browser capture", () => {
    expect(
      isLiveCaptureInProgress(
        buildRecording({
          status: RecordingStatus.PAUSED,
          client_status: ClientStatus.PAUSED,
        }),
      ),
    ).toBe(true);
  });

  it("still accepts a capture paused before client status was recorded", () => {
    expect(
      isLiveCaptureInProgress(
        buildRecording({
          status: RecordingStatus.PAUSED,
          client_status: undefined,
        }),
      ),
    ).toBe(true);
  });

  it("rejects a file import, which also sits in UPLOADING", () => {
    // The regression this guards: the old test was "UPLOADING and client status
    // is not UPLOADING", which an import's NULL satisfied, so a large import
    // rendered the live recording UI for the duration of its upload.
    expect(
      isLiveCaptureInProgress(
        buildRecording({
          status: RecordingStatus.UPLOADING,
          client_status: undefined,
        }),
      ),
    ).toBe(false);
  });

  it("rejects everything after the capture has been finalized", () => {
    expect(
      isLiveCaptureInProgress(
        buildRecording({
          status: RecordingStatus.QUEUED,
          client_status: ClientStatus.IDLE,
        }),
      ),
    ).toBe(false);
    expect(
      isLiveCaptureInProgress(
        buildRecording({
          status: RecordingStatus.PROCESSING,
          client_status: ClientStatus.IDLE,
        }),
      ),
    ).toBe(false);
  });

  it("rejects a missing recording", () => {
    expect(isLiveCaptureInProgress(null)).toBe(false);
    expect(isLiveCaptureInProgress(undefined)).toBe(false);
  });
});
