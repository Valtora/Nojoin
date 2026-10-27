import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { useState } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import LiveTranscriptPanel from "@/components/LiveTranscriptPanel";
import TranscriptLineMenu from "@/components/transcript/TranscriptLineMenu";
import type { TranscriptLineActions } from "@/components/transcript/_hooks/useTranscriptEditing";
import { isNoteSelection, NoteOutbox, type ComposerDraft, type NoteTarget, type OutboxDraft } from "@/lib/notes/noteOutbox";
import { resetNotesServer, server } from "@/test/fakeNotesServer";
import type { TranscriptSegment } from "@/types";
import LiveNoteComposer from "./LiveNoteComposer";

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  ...(await import("@/test/fakeNotesServer")).fakeNotesApi,
}));

const LINE: TranscriptSegment = {
  id: "u1", revision: 3, start: 12, end: 15.5, text: "Budget still growing",
  speaker: "UNKNOWN", settling_required: true, provisional: true, locked: false,
};

function LivePage({ segment = LINE, now, submit }: {
  segment?: TranscriptSegment; now: number; submit: (note: OutboxDraft) => string | null;
}) {
  const [draft, setDraft] = useState<ComposerDraft>({ text: "", mode: "typing", typingStartMs: null });
  const noteOnLine = (target: NoteTarget) => setDraft({ ...draft,
    selection: isNoteSelection(target) ? target : null,
    lineSpan: isNoteSelection(target) ? null : target });
  return <LiveTranscriptPanel hasLoaded segments={[segment]} onNoteOnLine={noteOnLine}
    footer={<LiveNoteComposer draft={draft} onDraftChange={setDraft} readAudioNow={() => now} onSubmit={submit} />} />;
}

beforeEach(resetNotesServer);

describe("notes on marked lines", () => {
  it("captures an open live line's timeline span at the action, with no quote or source lock", async () => {
    const outbox = new NoteOutbox("rec-1");
    const submit = (draft: OutboxDraft) => outbox.submit(draft);
    const view = render(<LivePage now={16_000} submit={submit} />);
    fireEvent.click(screen.getByRole("button", { name: "Actions for the line at 00:12" }));
    const item = screen.getByRole("menuitem", { name: "Note on this line" });
    expect(item).toHaveAttribute("aria-disabled", "false");
    fireEvent.click(item);
    expect(screen.getByTestId("note-line-chip")).toHaveTextContent("00:12–00:15");
    expect(screen.queryByTestId("note-selection-chip")).toBeNull();
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "Check the figure" } });
    view.rerender(<LivePage now={25_000} submit={submit} segment={{ ...LINE, end: 24, revision: 4 }} />);
    fireEvent.keyDown(screen.getByRole("textbox"), { key: "Enter" });
    await waitFor(() => expect(server.creates).toHaveLength(1));
    expect(server.creates[0]).toMatchObject({ kind: "point", body: "Check the figure",
      anchor_ms: 12_000, start_ms: 12_000, end_ms: 15_500 });
    expect(server.creates[0]).not.toHaveProperty("quoted_text");
    expect(server.creates[0]).not.toHaveProperty("source_utterances");
    await waitFor(() => expect(outbox.getSnapshot().saved).toHaveLength(1));
    expect(outbox.getSnapshot().saved[0]).toMatchObject({ kind: "point", quoted_text: null, end_ms: 15_500 });
  });

  it("keeps typing-start time across clock changes and resets it only after emptying", () => {
    const submit = vi.fn(() => null);
    const view = render(<LivePage now={1500} submit={submit} />);
    const box = screen.getByRole("textbox");
    fireEvent.change(box, { target: { value: "First" } });
    view.rerender(<LivePage now={9000} submit={submit} />);
    fireEvent.change(box, { target: { value: "First thought" } });
    fireEvent.keyDown(box, { key: "Enter" });
    expect(submit).toHaveBeenLastCalledWith({ kind: "point", body: "First thought", anchorMs: 1500 });
    fireEvent.change(box, { target: { value: "Discard" } });
    fireEvent.change(box, { target: { value: "" } });
    view.rerender(<LivePage now={12_000} submit={submit} />);
    fireEvent.change(box, { target: { value: "New thought" } });
    view.rerender(<LivePage now={20_000} submit={submit} />);
    fireEvent.keyDown(box, { key: "Enter" });
    expect(submit).toHaveBeenLastCalledWith({ kind: "point", body: "New thought", anchorMs: 12_000 });
  });

  it("removing a line placement restores an existing typing anchor", () => {
    const submit = vi.fn(() => null);
    function Composer() {
      const [draft, setDraft] = useState<ComposerDraft>({ text: "Existing thought", mode: "typing",
        typingStartMs: 1000, lineSpan: { startMs: 12_000, endMs: 15_500 } });
      return <LiveNoteComposer readAudioNow={() => 90_000} onSubmit={submit} draft={draft} onDraftChange={setDraft} />;
    }
    render(<Composer />);
    expect(screen.getByRole("combobox")).toHaveValue("line");
    fireEvent.click(screen.getByRole("button", { name: "Don't put the note on this line" }));
    expect(screen.getByRole("combobox")).toHaveValue("typing");
    fireEvent.keyDown(screen.getByRole("textbox"), { key: "Enter" });
    expect(submit).toHaveBeenCalledWith({ kind: "point", body: "Existing thought", anchorMs: 1000 });
  });

  it.each([true, false])("finished marked=%s keeps its intended line-note shape while the line changes", (marked) => {
    const add = vi.fn(() => null);
    const actions = {} as TranscriptLineActions;
    const props = { segment: { ...LINE, settling_required: marked }, segmentId: "u1", speakerName: "Alex",
      isSegmentReadOnly: true, actions, onAddNote: add, onPlayFrom: null,
      isEditingText: false, isChangingSpeaker: false };
    const view = render(<TranscriptLineMenu {...props} />);
    fireEvent.click(screen.getByRole("button", { name: "Actions for Alex's line at 00:12" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Note on this line" }));
    view.rerender(<TranscriptLineMenu {...props} segment={{ ...props.segment, end: 30, text: "New wording" }} />);
    const dialog = screen.getByRole("dialog");
    fireEvent.change(within(dialog).getByRole("textbox"), { target: { value: "A thought" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Save note" }));
    expect(add).toHaveBeenCalledWith(marked
      ? { kind: "point", body: "A thought", anchorMs: 12_000, span: { startMs: 12_000, endMs: 15_500 } }
      : { kind: "selection", body: "A thought", anchorMs: null,
        selection: { startMs: 12_000, endMs: 30_000, quotedText: "New wording" } });
  });
});
