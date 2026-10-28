import { act, cleanup, renderHook } from "@testing-library/react";
import { useLayoutEffect, useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ClientStatus, RecordingStatus, type Recording, type RecordingSpeaker, type TranscriptUtteranceList } from "@/types";
import { useRecordingTranscript } from "./useRecordingTranscript";

const mocks = vi.hoisted(() => ({ live: vi.fn(), published: vi.fn() }));
vi.mock("@/lib/api", () => ({
  getTranscriptUtterances: (...args: unknown[]) => mocks.published(...args),
  updateTranscriptUtteranceText: vi.fn(), updateTranscriptUtteranceSpeaker: vi.fn(),
}));
vi.mock("@/lib/api/transcript", () => ({ getTranscriptUtterances: (...args: unknown[]) => mocks.live(...args) }));
vi.mock("@/lib/notificationStore", () => ({ useNotificationStore: () => ({ addNotification: vi.fn() }) }));

const speaker = (name: string): RecordingSpeaker => ({
  id: name === "Old name" ? 1 : 2, diarization_label: "SPEAKER_00", local_name: name,
} as RecordingSpeaker);
const recording = (pending = true, status = RecordingStatus.PROCESSING): Recording => ({
  id: "rec-1", name: "Meeting", status, client_status: ClientStatus.IDLE,
  created_at: "2026-10-27", updated_at: "2026-10-27", meeting_uid: "meeting-1", audio_path: "/audio.wav",
  is_archived: false, is_deleted: false, previous_publication_available: pending,
  speakers: [speaker(pending ? "Old name" : "New name")],
  transcript: {
    id: 1, recording_id: "rec-1", created_at: "2026-10-27", updated_at: "2026-10-27",
    text: pending ? "Old words" : "", segments: pending
      ? [{ id: "old", text: "Old words", start: 0, end: 1, speaker: "SPEAKER_00" }] : [],
  },
});
// The same run after publication, while notes still run.
const published = (): Recording => ({ ...recording(false), reprocess_publication_complete: true });
const snapshot = (text = "Old words", revision = 4): TranscriptUtteranceList => ({
  recording_id: "rec-1", revision, speakers: [speaker(text === "Old words" ? "Old name" : "New name")],
  tombstones: [], utterances: text
    ? [{ id: text === "Old words" ? "old" : "new", text, start: 0, end: 1, speaker: "SPEAKER_00", revision }] : [],
});
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}
const advance = (ms = 0) => act(async () => { await vi.advanceTimersByTimeAsync(ms); });
function mount(initial = recording()) {
  const painted: string[] = [];
  const hook = renderHook(() => {
    const [value, setValue] = useState<Recording | null>(initial);
    const transcript = useRecordingTranscript(value, setValue);
    useLayoutEffect(() => {
      painted.push(`${transcript.transcriptSegments.map((line) => line.text).join("|")}/${
        transcript.transcriptSpeakers?.map((item) => item.local_name).join("|")}`);
    });
    return { ...transcript, setRecording: setValue };
  });
  return { ...hook, painted };
}

describe("retained recording publication", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    mocks.live.mockReset().mockResolvedValue(snapshot("Candidate words"));
    mocks.published.mockReset().mockResolvedValue(snapshot());
    vi.spyOn(console, "error").mockImplementation(() => {});
  });
  afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); });

  it.each([RecordingStatus.QUEUED, RecordingStatus.PROCESSING, RecordingStatus.ERROR])(
    "opens a retained %s result immediately while its full read waits", async (status) => {
      const pending = deferred<TranscriptUtteranceList>();
      mocks.published.mockReturnValue(pending.promise);
      const { result, painted } = mount(recording(true, status));
      await advance();
      expect(result.current.transcriptSegments[0]?.text).toBe("Old words");
      expect(result.current.usesInFlightTranscript).toBe(false);
      expect(mocks.live).not.toHaveBeenCalled();
      expect(mocks.published).toHaveBeenCalledTimes(1);
      expect(painted.every((value) => value === "Old words/Old name")).toBe(true);
      await act(async () => pending.resolve(snapshot()));
      expect(result.current.transcriptSegments[0]?.text).toBe("Old words");
    },
  );

  it.each(["New words", ""])("reloads the published reprocess result %j while optional notes still run", async (text) => {
    const full = deferred<TranscriptUtteranceList>();
    mocks.published.mockReturnValueOnce(full.promise);
    const published = { ...recording(false), reprocess_publication_complete: true };
    published.transcript = { ...published.transcript!, text, segments: text
      ? [{ id: "new", text, start: 0, end: 1, speaker: "SPEAKER_00" }] : [], transcript_status: "completed" };
    const { result } = mount(published);
    await advance();
    expect(result.current.transcriptSegments.map((item) => item.text)).toEqual(text ? [text] : []);
    expect(result.current.usesInFlightTranscript).toBe(false);
    expect(mocks.live).not.toHaveBeenCalled();
    await act(async () => full.resolve(snapshot(text, 0)));
    expect(result.current.transcriptSegments.map((item) => item.text)).toEqual(text ? [text] : []);
    expect(result.current.transcriptIsAuthoritative).toBe(true);
  });

  it.each([0, 4])("swaps the words on publication at revision %i", async (revision) => {
    const { result } = mount();
    await advance();
    const replacement = deferred<TranscriptUtteranceList>();
    mocks.published.mockReturnValueOnce(replacement.promise);
    act(() => result.current.setRecording(published()));
    await advance();
    expect(result.current.transcriptSegments[0]?.text).toBe("Old words");
    expect(mocks.published).toHaveBeenCalledTimes(2);
    expect(mocks.published.mock.calls[1][1]).toBeUndefined();
    await act(async () => replacement.resolve(snapshot("New words", revision)));
    expect(result.current.transcriptSegments[0]?.text).toBe("New words");
    expect(mocks.live).not.toHaveBeenCalled();
  });

  it("accepts an empty replacement without restoring the old metadata", async () => {
    const { result } = mount();
    await advance();
    mocks.published.mockResolvedValue(snapshot("", 0));
    const completed = { ...published(), transcript: recording(true).transcript };
    act(() => result.current.setRecording(completed));
    await advance();
    expect(result.current.transcriptSegments).toEqual([]);
    expect(result.current.transcriptIsAuthoritative).toBe(true);
    act(() => result.current.setRecording({ ...completed, status: RecordingStatus.PROCESSED,
      reprocess_publication_complete: false }));
    await advance();
    expect(result.current.transcriptSegments).toEqual([]);
    expect(result.current.hasRetainedPublication).toBe(false);
  });

  it("refreshes the replacement again once the run has ended, as for any recording", async () => {
    const { result } = mount();
    await advance();
    mocks.published.mockResolvedValue(snapshot("New words"));
    act(() => result.current.setRecording(published()));
    await advance();
    act(() => result.current.setRecording({ ...published(), status: RecordingStatus.PROCESSED,
      reprocess_publication_complete: false }));
    await advance();
    const reads = mocks.published.mock.calls.length;
    // Regenerating notes later polls the transcript, as on any recording.
    const regenerating = { ...published(), status: RecordingStatus.PROCESSED, reprocess_publication_complete: false };
    regenerating.transcript = { ...regenerating.transcript!, notes_status: "generating" };
    act(() => result.current.setRecording(regenerating));
    await advance(3_000);
    expect(result.current.hasRetainedPublication).toBe(false);
    expect(mocks.published.mock.calls.length).toBeGreaterThan(reads);
  });

  it("holds the old snapshot across failed reads, then retries publication after unchanged metadata", async () => {
    const { result } = mount();
    await advance();
    mocks.published.mockRejectedValueOnce(new Error("offline")).mockResolvedValue(snapshot("New words"));
    act(() => result.current.setRecording(published()));
    await advance();
    expect(result.current.transcriptSegments[0]?.text).toBe("Old words");
    await advance(3_000);
    expect(result.current.transcriptSegments[0]?.text).toBe("New words");
  });

  it("drops a retained read that returns after its replacement", async () => {
    const old = deferred<TranscriptUtteranceList>();
    mocks.published.mockReturnValueOnce(old.promise);
    const { result } = mount();
    await advance();
    mocks.published.mockResolvedValue(snapshot("New words"));
    act(() => result.current.setRecording(published()));
    await advance();
    expect(result.current.transcriptSegments[0]?.text).toBe("New words");
    await act(async () => old.resolve(snapshot("Obsolete words")));
    expect(result.current.transcriptSegments[0]?.text).toBe("New words");
  });

  it("drops the initial full read if publication overtakes it", async () => {
    const old = deferred<TranscriptUtteranceList>();
    const replacement = deferred<TranscriptUtteranceList>();
    mocks.published.mockReturnValueOnce(old.promise).mockReturnValueOnce(replacement.promise);
    const { result } = mount();
    await advance();
    act(() => result.current.setRecording(published()));
    await advance();
    expect(result.current.transcriptSegments[0]?.text).toBe("Old words");
    await act(async () => old.resolve(snapshot("Obsolete words")));
    expect(result.current.transcriptSegments[0]?.text).toBe("Old words");
    await act(async () => replacement.resolve(snapshot("New words")));
    expect(result.current.transcriptSegments[0]?.text).toBe("New words");
  });

  it("does not revive the capture feed when the same open recording is reprocessed after handoff", async () => {
    const { result } = mount({ ...recording(false, RecordingStatus.UPLOADING), client_status: ClientStatus.RECORDING });
    await advance();
    act(() => result.current.setRecording(recording(false, RecordingStatus.PROCESSED)));
    await advance();
    expect(result.current.transcriptSegments[0]?.text).toBe("Old words");
    const liveReads = mocks.live.mock.calls.length;
    mocks.published.mockReturnValueOnce(deferred<TranscriptUtteranceList>().promise);
    act(() => result.current.setRecording(recording(true)));
    await advance();
    expect(result.current.usesInFlightTranscript).toBe(false);
    expect(result.current.transcriptSegments[0]?.text).toBe("Old words");
    expect(mocks.live).toHaveBeenCalledTimes(liveReads);
  });
});
