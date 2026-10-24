import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  LIVE_TRANSCRIPT_FULL_READ_EVERY_POLLS,
  LIVE_TRANSCRIPT_POLL_INTERVAL_MS,
  LIVE_TRANSCRIPT_READ_TIMEOUT_MS,
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
    expect(getTranscriptUtterances).toHaveBeenCalledWith("rec-1", undefined, expect.any(AbortSignal));
    expect(result.current.segments.map((segment) => segment.text)).toEqual([
      "first line",
    ]);

    await advanceOnePoll();

    // The cursor advances so each poll asks only for what it has not seen.
    expect(getTranscriptUtterances).toHaveBeenLastCalledWith("rec-1", 4, expect.any(AbortSignal));
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

  it("reads the whole transcript now and then, so a change the cursor skipped shows", async () => {
    // A label committed after a later-numbered edit: no delta returns it.
    getTranscriptUtterances
      .mockResolvedValueOnce(
        buildDelta(4, [
          buildUtterance("a", 0, "first line", 3),
          buildUtterance("b", 5, "second line", 4),
        ]),
      )
      .mockResolvedValueOnce(buildDelta(8, []));

    const { result } = renderHook(() => useLiveTranscript(buildRecording()));
    await flushPendingRequests();
    await advanceOnePoll();

    getTranscriptUtterances.mockImplementation(
      async (_recordingId: string, afterRevision?: number) =>
        afterRevision === undefined
          ? buildDelta(8, [
              { ...buildUtterance("a", 0, "first line", 5), speaker: "LIVE_PROVISIONAL_1" },
              buildUtterance("b", 5, "second line", 4),
            ])
          : buildDelta(8, []),
    );
    for (let poll = 2; poll < LIVE_TRANSCRIPT_FULL_READ_EVERY_POLLS; poll += 1) {
      await advanceOnePoll();
      expect(getTranscriptUtterances).toHaveBeenLastCalledWith("rec-1", 8, expect.any(AbortSignal));
    }
    expect(result.current.segments[0].speaker).toBe("UNKNOWN");

    await advanceOnePoll();

    expect(getTranscriptUtterances).toHaveBeenLastCalledWith("rec-1", undefined, expect.any(AbortSignal));
    expect(result.current.segments.map((segment) => segment.speaker)).toEqual([
      "LIVE_PROVISIONAL_1",
      "UNKNOWN",
    ]);
  });

  it("skips a tick while a read is in flight, so a slow full read cannot undo a newer delta", async () => {
    // The server's line changes after the 10th poll's full read has started.
    const server = { revision: 4, text: "older" };
    let landFullRead: (delta: ReturnType<typeof buildDelta>) => void = () => {};
    getTranscriptUtterances.mockResolvedValueOnce(
      buildDelta(4, [buildUtterance("a", 0, "older", 4)]),
    );
    const shown: string[] = [];
    renderHook(() => {
      const live = useLiveTranscript(buildRecording());
      shown.push(live.segments.map((segment) => segment.text).join());
      return live;
    });
    await flushPendingRequests();
    getTranscriptUtterances.mockImplementation(
      async (_recordingId: string, afterRevision?: number) => {
        if (afterRevision === undefined) {
          return new Promise((resolve) => {
            landFullRead = resolve;
          });
        }
        return buildDelta(
          server.revision,
          afterRevision < server.revision
            ? [buildUtterance("a", 0, server.text, server.revision)]
            : [],
        );
      },
    );
    for (let poll = 1; poll <= LIVE_TRANSCRIPT_FULL_READ_EVERY_POLLS; poll += 1) {
      await advanceOnePoll();
    }
    expect(getTranscriptUtterances).toHaveBeenLastCalledWith("rec-1", undefined, expect.any(AbortSignal));
    const calls = getTranscriptUtterances.mock.calls.length;
    Object.assign(server, { revision: 6, text: "newer" });

    await advanceOnePoll();
    expect(getTranscriptUtterances).toHaveBeenCalledTimes(calls);
    await act(async () => {
      landFullRead(buildDelta(4, [buildUtterance("a", 0, "older", 4)]));
    });
    await advanceOnePoll();

    expect(getTranscriptUtterances).toHaveBeenLastCalledWith("rec-1", 4, expect.any(AbortSignal));
    expect(shown.at(-1)).toBe("newer");
    expect(shown.slice(shown.indexOf("newer"))).not.toContain("older");
  });

  it("abandons a read that never answers, so the feed keeps going", async () => {
    // A dropped connection: the API client sets no timeout of its own.
    let stalled = false;
    let hung: AbortSignal | undefined;
    getTranscriptUtterances.mockImplementation(
      (_recordingId: string, afterRevision?: number, signal?: AbortSignal) => {
        if (afterRevision === 4 && !stalled) {
          stalled = true;
          hung = signal;
          return new Promise(() => {});
        }
        return Promise.resolve(
          afterRevision === undefined
            ? buildDelta(4, [buildUtterance("a", 0, "first line", 4)])
            : buildDelta(5, [buildUtterance("b", 5, "after the stall", 5)]),
        );
      },
    );

    const { result } = renderHook(() => useLiveTranscript(buildRecording()));
    await flushPendingRequests();
    await advanceOnePoll();
    const calls = getTranscriptUtterances.mock.calls.length;

    await act(async () => {
      await vi.advanceTimersByTimeAsync(LIVE_TRANSCRIPT_READ_TIMEOUT_MS);
    });
    await advanceOnePoll();

    expect(getTranscriptUtterances.mock.calls.length).toBeGreaterThan(calls);
    expect(result.current.segments.map((segment) => segment.text)).toEqual([
      "first line",
      "after the stall",
    ]);
    // The abandoned request is cancelled, not left open.
    expect(hung?.aborted).toBe(true);
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

  it("shows the live names every read carries, and a rename's at once", async () => {
    getTranscriptUtterances
      .mockResolvedValueOnce({
        ...buildDelta(4, [buildUtterance("a", 0, "first line", 4)]),
        live_speaker_names: { LIVE_PROVISIONAL_1: "Alex" },
      })
      // A delta with no new line still carries the names whole.
      .mockResolvedValueOnce({
        ...buildDelta(4, []),
        live_speaker_names: { LIVE_PROVISIONAL_1: "Alexis", LIVE_PROVISIONAL_2: "Blair" },
      });

    const { result } = renderHook(() => useLiveTranscript(buildRecording()));

    await flushPendingRequests();
    expect(result.current.liveSpeakerNames).toEqual({ LIVE_PROVISIONAL_1: "Alex" });

    act(() => {
      result.current.applyLiveSpeakerNames({ LIVE_PROVISIONAL_1: "Alexis" });
    });
    expect(result.current.liveSpeakerNames).toEqual({ LIVE_PROVISIONAL_1: "Alexis" });

    await advanceOnePoll();
    expect(result.current.liveSpeakerNames).toEqual({
      LIVE_PROVISIONAL_1: "Alexis",
      LIVE_PROVISIONAL_2: "Blair",
    });
    expect(result.current.segments.map((segment) => segment.text)).toEqual([
      "first line",
    ]);
  });

  it("keeps the same names object across idle polls, so the panel does not re-render", async () => {
    // Every answer is a new object, as from the network.
    let names: Record<string, string> = {};
    getTranscriptUtterances.mockImplementation(async () => ({
      ...buildDelta(4, []),
      live_speaker_names: { ...names },
    }));

    const { result } = renderHook(() => useLiveTranscript(buildRecording()));
    await flushPendingRequests();
    const empty = result.current.liveSpeakerNames;
    await advanceOnePoll();
    expect(result.current.liveSpeakerNames).toBe(empty);

    names = { LIVE_PROVISIONAL_1: "Alex" };
    await advanceOnePoll();
    const named = result.current.liveSpeakerNames;
    expect(named).toEqual({ LIVE_PROVISIONAL_1: "Alex" });
    await advanceOnePoll();
    expect(result.current.liveSpeakerNames).toBe(named);

    names = { LIVE_PROVISIONAL_1: "Alexis" };
    await advanceOnePoll();
    expect(result.current.liveSpeakerNames).toEqual({ LIVE_PROVISIONAL_1: "Alexis" });
  });

  it("clears the live names when a later poll returns an empty map", async () => {
    getTranscriptUtterances
      .mockResolvedValueOnce({
        ...buildDelta(4, []),
        live_speaker_names: { LIVE_PROVISIONAL_1: "Alex" },
      })
      .mockResolvedValueOnce({
        ...buildDelta(4, []),
        live_speaker_names: {},
      });

    const { result } = renderHook(() => useLiveTranscript(buildRecording()));
    const empty = result.current.liveSpeakerNames;

    await flushPendingRequests();
    expect(result.current.liveSpeakerNames).toEqual({ LIVE_PROVISIONAL_1: "Alex" });

    await advanceOnePoll();
    expect(result.current.liveSpeakerNames).toEqual({});
    expect(result.current.liveSpeakerNames).toBe(empty);
  });
});
