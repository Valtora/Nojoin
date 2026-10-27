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

  it.each([0, 4])("swaps words and fresh speaker identity together on publication at revision %i", async (revision) => {
    const { result, painted } = mount();
    await advance();
    const replacement = deferred<TranscriptUtteranceList>();
    mocks.published.mockReturnValueOnce(replacement.promise);
    act(() => result.current.setRecording(recording(false)));
    await advance();
    expect(result.current.transcriptSegments[0]?.text).toBe("Old words");
    expect(result.current.transcriptSpeakers?.[0]?.local_name).toBe("Old name");
    expect(mocks.published).toHaveBeenCalledTimes(2);
    expect(mocks.published.mock.calls[1][1]).toBeUndefined();
    await act(async () => replacement.resolve(snapshot("New words", revision)));
    expect(result.current.transcriptSegments[0]?.text).toBe("New words");
    expect(result.current.transcriptSpeakers?.[0]?.local_name).toBe("New name");
    expect(painted.every((value) => ["Old words/Old name", "New words/New name"].includes(value))).toBe(true);
    expect(mocks.live).not.toHaveBeenCalled();
  });

  it("accepts an empty replacement without restoring the old metadata", async () => {
    const { result } = mount();
    await advance();
    mocks.published.mockResolvedValue(snapshot("", 0));
    const completed = { ...recording(false), transcript: recording(true).transcript };
    act(() => result.current.setRecording(completed));
    await advance();
    expect(result.current.transcriptSegments).toEqual([]);
    expect(result.current.transcriptIsAuthoritative).toBe(true);
    act(() => result.current.setRecording({ ...completed, status: RecordingStatus.PROCESSED }));
    await advance();
    expect(result.current.transcriptSegments).toEqual([]);
    expect(result.current.transcriptIsAuthoritative).toBe(true);
  });

  it("holds the old snapshot across failed reads, then retries publication after unchanged metadata", async () => {
    const { result } = mount();
    await advance();
    mocks.published.mockRejectedValueOnce(new Error("offline")).mockResolvedValue(snapshot("New words"));
    act(() => result.current.setRecording(recording(false)));
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
    act(() => result.current.setRecording(recording(false)));
    await advance();
    expect(result.current.transcriptSegments[0]?.text).toBe("New words");
    await act(async () => old.resolve(snapshot("Obsolete words")));
    expect(result.current.transcriptSegments[0]?.text).toBe("New words");
  });

  it("holds the complete metadata snapshot if publication wins the initial full read", async () => {
    const old = deferred<TranscriptUtteranceList>();
    const replacement = deferred<TranscriptUtteranceList>();
    mocks.published.mockReturnValueOnce(old.promise).mockReturnValueOnce(replacement.promise);
    const { result, painted } = mount();
    await advance();
    act(() => result.current.setRecording(recording(false)));
    await advance();
    expect(result.current.transcriptSegments[0]?.text).toBe("Old words");
    expect(result.current.transcriptSpeakers?.[0]?.local_name).toBe("Old name");
    await act(async () => old.resolve(snapshot("Obsolete words")));
    expect(result.current.transcriptSegments[0]?.text).toBe("Old words");
    await act(async () => replacement.resolve(snapshot("New words")));
    expect(painted.every((value) => ["Old words/Old name", "New words/New name"].includes(value))).toBe(true);
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
