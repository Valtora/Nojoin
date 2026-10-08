import { describe, expect, it } from "vitest";

import { RecordingStatus, type Recording } from "@/types";

import {
  AUDIO_RECHECK_INTERVAL_MS,
  AUDIO_RECHECK_WINDOW_MS,
  hasPolledRecordingChanged,
  recordingPollIntervalMs,
  shouldPollRecordingUpdates,
} from "./recordingDetailUtils";

const recordingWith = (overrides: Partial<Recording>) =>
  ({
    id: "rec-1",
    name: "Weekly sync",
    status: RecordingStatus.PROCESSED,
    has_proxy: false,
    speakers: [],
    ...overrides,
  }) as unknown as Recording;

describe("polling a recording with no audio and no proxy", () => {
  it("stops the fast proxy poll, since no proxy is coming", () => {
    expect(shouldPollRecordingUpdates(recordingWith({ has_audio: false }))).toBe(
      false,
    );
    expect(shouldPollRecordingUpdates(recordingWith({ has_audio: true }))).toBe(
      true,
    );
  });

  it("still re-checks slowly, so audio a restore moves in later shows up", () => {
    expect(recordingPollIntervalMs(recordingWith({ has_audio: false }))).toBe(
      AUDIO_RECHECK_INTERVAL_MS,
    );
    expect(recordingPollIntervalMs(recordingWith({ has_audio: true }))).toBe(1000);
  });

  it("stops re-checking once the audio has been unavailable for the whole window", () => {
    const missing = recordingWith({ has_audio: false });

    expect(recordingPollIntervalMs(missing, AUDIO_RECHECK_WINDOW_MS - 1)).toBe(
      AUDIO_RECHECK_INTERVAL_MS,
    );
    expect(recordingPollIntervalMs(missing, AUDIO_RECHECK_WINDOW_MS)).toBeNull();
    // The window bounds only the re-check, never the wait for a proxy.
    expect(
      recordingPollIntervalMs(
        recordingWith({ has_audio: true }),
        AUDIO_RECHECK_WINDOW_MS,
      ),
    ).toBe(1000);
  });

  it("does not poll a settled recording that has its audio", () => {
    expect(
      recordingPollIntervalMs(recordingWith({ has_proxy: true, has_audio: true })),
    ).toBeNull();
    // Not checked by the endpoint (null) is not the same as missing.
    expect(
      recordingPollIntervalMs(recordingWith({ has_proxy: true, has_audio: null })),
    ).toBeNull();
  });

  it("treats the arrival of the audio as a change worth re-rendering", () => {
    const before = recordingWith({ has_audio: false });

    expect(hasPolledRecordingChanged(before, { ...before })).toBe(false);
    expect(hasPolledRecordingChanged(before, { ...before, has_audio: true })).toBe(
      true,
    );
  });
});
