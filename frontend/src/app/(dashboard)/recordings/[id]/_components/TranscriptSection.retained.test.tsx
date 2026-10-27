import { useState } from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import TranscriptSection from "@/test/TranscriptSectionWithEdits";
import { RecordingStatus, type Recording, type RecordingSpeaker, type TranscriptUtteranceList } from "@/types";
import { useRecordingTranscript } from "../_hooks/useRecordingTranscript";

const mocks = vi.hoisted(() => ({
  published: vi.fn(), live: vi.fn(), rename: vi.fn(), edit: vi.fn(), dismiss: vi.fn(),
  undo: vi.fn(), redo: vi.fn(), replace: vi.fn(),
}));
vi.mock("@/lib/api", async (original) => ({
  ...await original<typeof import("@/lib/api")>(),
  getTranscriptUtterances: (...args: unknown[]) => mocks.published(...args),
  dismissSpeakerName: (...args: unknown[]) => mocks.dismiss(...args),
}));
vi.mock("@/lib/api/transcript", async (original) => ({
  ...await original<typeof import("@/lib/api/transcript")>(),
  getTranscriptUtterances: (...args: unknown[]) => mocks.live(...args),
}));

const speaker = (name: string): RecordingSpeaker => ({
  id: name === "Old name" ? 1 : 2, diarization_label: "SPEAKER_00", local_name: name, name_source: "voiceprint",
} as RecordingSpeaker);
const snapshot = (text = "Old words"): TranscriptUtteranceList => ({
  recording_id: "rec-1", revision: 4, speakers: [speaker(text === "Old words" ? "Old name" : "New name")],
  tombstones: [], utterances: text
    ? [{ id: text === "Old words" ? "old" : "new", text, start: 0, end: 1, speaker: "SPEAKER_00", revision: 4 }] : [],
});
const recording = (retained: boolean): Recording => ({
  id: "rec-1", name: "Meeting", status: retained ? RecordingStatus.PROCESSING : RecordingStatus.PROCESSED,
  previous_publication_available: retained, speakers: [speaker("Old name")],
  transcript: { text: "Old words", segments: snapshot().utterances },
} as unknown as Recording);
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
}

/** The real reader and section, with only the network and write boundary replaced. */
function Harness({ initial, terminal = RecordingStatus.PROCESSED }: {
  initial: Recording; terminal?: RecordingStatus;
}) {
  const [value, setValue] = useState<Recording | null>(initial);
  const transcript = useRecordingTranscript(value, setValue);
  if (!value) return null;
  const names = Object.fromEntries((transcript.transcriptSpeakers ?? []).map((item) =>
    [item.diarization_label, item.local_name ?? "Speaker 1"]));
  return <>
    <button onClick={() => setValue({ ...value, status: RecordingStatus.PROCESSING,
      previous_publication_available: true, reprocess_publication_complete: false })}>Reprocess</button>
    <button onClick={() => setValue({ ...value, status: terminal, previous_publication_available: false,
      reprocess_publication_complete: true, speakers: [speaker("New name")] })}>Publish</button>
    <button onClick={() => void transcript.syncTranscriptState("full")}>Retry read</button>
    <output aria-label="Reader authority">{String(transcript.transcriptIsAuthoritative)}</output>
    <output aria-label="Deferred lines">{transcript.deferredTranscriptUtteranceIds.join(",")}</output>
    <TranscriptSection active recording={value} timelineNotes={[]} transcriptSegments={transcript.transcriptSegments}
      transcriptIsAuthoritative={transcript.transcriptIsAuthoritative} currentTime={-1} isPlaying={false}
      speakerMap={names} speakerColors={{}} globalSpeakers={[]} canUndo canRedo
      deferredTranscriptUtteranceIds={transcript.deferredTranscriptUtteranceIds}
      onPlaySegment={vi.fn()} onPause={vi.fn()} onResume={vi.fn()} onRenameSpeaker={mocks.rename}
      onRefresh={vi.fn()} onUpdateSegmentSpeaker={vi.fn()} onUpdateSegmentText={mocks.edit}
      onFindAndReplace={mocks.replace} onUndo={mocks.undo} onRedo={mocks.redo} onExport={vi.fn()}
      onActiveEditUtteranceChange={transcript.setActiveTranscriptEditId} />
  </>;
}

describe("retained transcript editing authority", () => {
  beforeEach(() => {
    mocks.published.mockReset().mockResolvedValue(snapshot());
    mocks.live.mockReset().mockResolvedValue(snapshot("Candidate words"));
    mocks.rename.mockReset().mockResolvedValue(true);
    mocks.edit.mockReset().mockResolvedValue(true);
    mocks.dismiss.mockReset().mockResolvedValue([]);
    mocks.undo.mockReset();
    mocks.redo.mockReset();
    mocks.replace.mockReset();
  });
  afterEach(() => vi.restoreAllMocks());

  it.each([RecordingStatus.PROCESSED, RecordingStatus.ERROR])(
    "keeps the old snapshot read-only at terminal %s until the replacement is accepted", async (terminal) => {
      const replacement = deferred<TranscriptUtteranceList>();
      render(<Harness initial={recording(true)} terminal={terminal} />);
      await waitFor(() => expect(screen.getByLabelText("Reader authority")).toHaveTextContent("true"));
      mocks.published.mockReturnValueOnce(replacement.promise);
      fireEvent.click(screen.getByRole("button", { name: "Publish" }));
      await waitFor(() => expect(mocks.published).toHaveBeenCalledTimes(2));
      expect(screen.getByLabelText("Reader authority")).toHaveTextContent("false");
      expect(screen.getByText("Old words")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Undo" })).toBeDisabled();
      expect(screen.getByRole("button", { name: "Redo" })).toBeDisabled();
      fireEvent.click(screen.getByRole("button", { name: /Actions for Old name/ }));
      expect(screen.getAllByRole("menuitem").map((item) => item.textContent))
        .toEqual(["Note on this line", "Play from here"]);
      expect(screen.getByRole("button", { name: "Old name" })).toBeDisabled();
      fireEvent.keyDown(screen.getByRole("menu"), { key: "Escape" });
      await act(async () => replacement.resolve(snapshot("New words")));
      expect(screen.queryByText("Old words")).not.toBeInTheDocument();
      expect(screen.getByText("New words")).toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: /Actions for New name/ }));
      expect(screen.getByRole("button", { name: "New name" })).toBeEnabled();
      expect(screen.getByRole("menuitem", { name: "Edit text" })).toBeInTheDocument();
      expect(mocks.rename).not.toHaveBeenCalled();
      expect(mocks.live).not.toHaveBeenCalled();
      fireEvent.click(screen.getByRole("button", { name: "Undo" }));
      fireEvent.click(screen.getByRole("button", { name: "Redo" }));
      expect(mocks.undo).toHaveBeenCalledOnce();
      expect(mocks.redo).toHaveBeenCalledOnce();
    },
  );

  it("holds edits across a failed replacement read and enables them after a successful retry", async () => {
    const error = vi.spyOn(console, "error").mockImplementation(() => {});
    render(<Harness initial={recording(true)} />);
    await waitFor(() => expect(screen.getByLabelText("Reader authority")).toHaveTextContent("true"));
    mocks.published.mockRejectedValueOnce(new Error("offline"));
    fireEvent.click(screen.getByRole("button", { name: "Publish" }));
    await waitFor(() => expect(error).toHaveBeenCalled());
    expect(screen.getByText("Old words")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Actions for Old name/ }));
    expect(screen.queryByRole("menuitem", { name: "Edit text" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Old name" })).toBeDisabled();
    fireEvent.keyDown(screen.getByRole("menu"), { key: "Escape" });
    mocks.published.mockResolvedValueOnce(snapshot("New words"));
    fireEvent.click(screen.getByRole("button", { name: "Retry read" }));
    await screen.findByText("New words");
    fireEvent.click(screen.getByRole("button", { name: /Actions for New name/ }));
    expect(screen.getByRole("button", { name: "New name" })).toBeEnabled();
  });

  it("keeps an ordinary processed recording editable when no handoff authority is required", async () => {
    render(<Harness initial={recording(false)} />);
    await waitFor(() => expect(mocks.published).toHaveBeenCalledOnce());
    expect(screen.getByLabelText("Reader authority")).toHaveTextContent("false");
    fireEvent.click(screen.getByRole("button", { name: /Actions for Old name/ }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Edit text" }));
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "Corrected words" } });
    fireEvent.keyDown(screen.getByRole("textbox"), { key: "Enter" });
    await waitFor(() => expect(mocks.edit)
      .toHaveBeenCalledWith(expect.objectContaining({ id: "old" }), "Corrected words"));
  });

  it("blocks a rename draft opened before reprocessing when terminal metadata arrives first", async () => {
    render(<Harness initial={recording(false)} />);
    await waitFor(() => expect(mocks.published).toHaveBeenCalledOnce());
    fireEvent.doubleClick(screen.getByRole("button", { name: "Old name" }));
    const editor = screen.getByRole("textbox");
    fireEvent.change(editor, { target: { value: "My name" } });
    fireEvent.click(screen.getByRole("button", { name: "Reprocess" }));
    await waitFor(() => expect(mocks.published).toHaveBeenCalledTimes(2));
    const replacement = deferred<TranscriptUtteranceList>();
    mocks.published.mockReturnValueOnce(replacement.promise);
    fireEvent.click(screen.getByRole("button", { name: "Publish" }));
    await waitFor(() => expect(mocks.published).toHaveBeenCalledTimes(3));
    fireEvent.keyDown(editor, { key: "Enter" });
    await act(async () => {});
    expect(mocks.rename).not.toHaveBeenCalled();
    expect(editor).toHaveValue("My name");
  });

  it("blocks a name dismissal from a speaker popover retained through publication", async () => {
    render(<Harness initial={recording(false)} />);
    await waitFor(() => expect(mocks.published).toHaveBeenCalledOnce());
    fireEvent.click(screen.getByRole("button", { name: "Old name" }));
    expect(screen.getByRole("button", { name: "Not Old name" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Reprocess" }));
    await waitFor(() => expect(mocks.published).toHaveBeenCalledTimes(2));
    mocks.published.mockReturnValueOnce(deferred<TranscriptUtteranceList>().promise);
    fireEvent.click(screen.getByRole("button", { name: "Publish" }));
    await waitFor(() => expect(mocks.published).toHaveBeenCalledTimes(3));
    fireEvent.click(screen.getByRole("button", { name: "Not Old name" }));
    await act(async () => {});
    expect(mocks.dismiss).not.toHaveBeenCalled();
  });

  it("waits for an open old editor to release its deferred row after the replacement full read", async () => {
    render(<Harness initial={recording(false)} />);
    await waitFor(() => expect(mocks.published).toHaveBeenCalledOnce());
    fireEvent.click(screen.getByRole("button", { name: /Actions for Old name/ }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Edit text" }));
    const editor = screen.getByRole("textbox");
    fireEvent.change(editor, { target: { value: "My correction" } });
    fireEvent.click(screen.getByRole("button", { name: "Reprocess" }));
    await waitFor(() => expect(mocks.published).toHaveBeenCalledTimes(2));
    const replacement = deferred<TranscriptUtteranceList>();
    mocks.published.mockReturnValueOnce(replacement.promise);
    fireEvent.click(screen.getByRole("button", { name: "Publish" }));
    await waitFor(() => expect(mocks.published).toHaveBeenCalledTimes(3));
    await act(async () => replacement.resolve(snapshot("New words")));
    expect(screen.getByLabelText("Reader authority")).toHaveTextContent("true");
    expect(screen.getByLabelText("Deferred lines")).toHaveTextContent("old");
    const retainedEditor = screen.getByRole("textbox");
    expect(retainedEditor).toHaveValue("My correction");
    expect(screen.getByRole("button", { name: "Undo" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Redo" })).toBeDisabled();
    for (const button of screen.getAllByRole("button", { name: "New name" })) expect(button).toBeDisabled();
    fireEvent.keyDown(retainedEditor, { key: "Enter" });
    await act(async () => {});
    expect(mocks.edit).not.toHaveBeenCalled();
    expect(mocks.rename).not.toHaveBeenCalled();
    expect(mocks.dismiss).not.toHaveBeenCalled();
    fireEvent.keyDown(retainedEditor, { key: "Escape" });
    await waitFor(() => expect(screen.getByLabelText("Deferred lines")).toBeEmptyDOMElement());
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(screen.getByText("New words")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "New name" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Undo" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Redo" })).toBeEnabled();
  });
});
