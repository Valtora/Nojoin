import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  LIVE_TRANSCRIPT_POLL_INTERVAL_MS,
  useLiveTranscript,
} from "./useLiveTranscript";
import { ClientStatus, Recording, RecordingStatus } from "@/types";

const getTranscriptUtterances = vi.fn();

vi.mock("@/lib/api/transcript", () => ({
  getTranscriptUtterances: (...args: unknown[]) =>
    getTranscriptUtterances(...args),
}));

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

const buildUtterance = (
  id: string,
  start: number,
  text: string,
  revision: number,
) => ({
  id,
  start,
  end: start + 1,
  start_ms: start * 1000,
  end_ms: (start + 1) * 1000,
  text,
  speaker: "UNKNOWN",
  state: "provisional",
  revision,
  provisional: true,
  segment_source: "live",
});

const buildDelta = (
  revision: number,
  utterances: ReturnType<typeof buildUtterance>[],
) => ({
  recording_id: "rec-1",
  revision,
  utterances,
  tombstones: [],
  speakers: [],
});

/**
 * Settle pending promises without moving the clock.
 *
 * Testing Library's waitFor advances fake timers itself, which would fire extra
 * poll ticks and make the call sequence untestable. Drive the clock explicitly
 * instead so each assertion sees exactly the polls it asked for.
 */
const flushPendingRequests = () =>
  act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });

const advanceOnePoll = () =>
  act(async () => {
    await vi.advanceTimersByTimeAsync(LIVE_TRANSCRIPT_POLL_INTERVAL_MS);
  });

describe("useLiveTranscript", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    getTranscriptUtterances.mockReset();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("does not poll at all when there is no live capture", () => {
    renderHook(() =>
      useLiveTranscript(
        buildRecording({
          status: RecordingStatus.UPLOADING,
          client_status: undefined,
        }),
      ),
    );

    expect(getTranscriptUtterances).not.toHaveBeenCalled();
  });

  it("loads the transcript so far, then polls for deltas from that revision", async () => {
    getTranscriptUtterances
      .mockResolvedValueOnce(
        buildDelta(4, [buildUtterance("a", 0, "first line", 4)]),
      )
      .mockResolvedValueOnce(
        buildDelta(7, [buildUtterance("b", 5, "second line", 7)]),
      );

    const { result } = renderHook(() => useLiveTranscript(buildRecording()));

    await flushPendingRequests();
    expect(result.current.hasLoaded).toBe(true);
    expect(getTranscriptUtterances).toHaveBeenCalledWith("rec-1", undefined);
    expect(result.current.segments.map((segment) => segment.text)).toEqual([
      "first line",
    ]);

    await advanceOnePoll();

    // The cursor advances so each poll asks only for what it has not seen.
    expect(getTranscriptUtterances).toHaveBeenLastCalledWith("rec-1", 4);
    expect(result.current.segments.map((segment) => segment.text)).toEqual([
      "first line",
      "second line",
    ]);
  });

  it("keeps the transcript and keeps polling when a request fails", async () => {
    const consoleError = vi
      .spyOn(console, "error")
      .mockImplementation(() => undefined);

    getTranscriptUtterances
      .mockResolvedValueOnce(
        buildDelta(2, [buildUtterance("a", 0, "survives", 2)]),
      )
      .mockRejectedValueOnce(new Error("network blip"))
      .mockResolvedValueOnce(
        buildDelta(5, [buildUtterance("b", 5, "recovered", 5)]),
      );

    const { result } = renderHook(() => useLiveTranscript(buildRecording()));
    await flushPendingRequests();

    await advanceOnePoll();

    // A transient failure must not blank the panel over a running meeting.
    expect(result.current.segments.map((segment) => segment.text)).toEqual([
      "survives",
    ]);
    expect(consoleError).toHaveBeenCalled();

    await advanceOnePoll();

    expect(result.current.segments.map((segment) => segment.text)).toEqual([
      "survives",
      "recovered",
    ]);
  });

  it("stops polling once the capture is finalized", async () => {
    getTranscriptUtterances.mockResolvedValue(
      buildDelta(1, [buildUtterance("a", 0, "line", 1)]),
    );

    const { result, rerender } = renderHook(
      ({ recording }: { recording: Recording }) =>
        useLiveTranscript(recording),
      { initialProps: { recording: buildRecording() } },
    );

    await flushPendingRequests();
    expect(result.current.hasLoaded).toBe(true);
    const callsWhileLive = getTranscriptUtterances.mock.calls.length;

    rerender({
      recording: buildRecording({
        status: RecordingStatus.QUEUED,
        client_status: ClientStatus.IDLE,
      }),
    });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(LIVE_TRANSCRIPT_POLL_INTERVAL_MS * 3);
    });

    expect(getTranscriptUtterances.mock.calls.length).toBe(callsWhileLive);
  });
});
