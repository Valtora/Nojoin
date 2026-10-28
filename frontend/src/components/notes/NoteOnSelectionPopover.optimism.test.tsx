import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { updateTimelineNote } from "@/lib/api";
import { forgetNoteOutboxes } from "@/lib/notes/noteOutboxes";
import { useNoteEdits } from "@/lib/notes/useNoteEdits";
import type { TimelineNote } from "@/types";

import TranscriptNoteEditor, { type EditingNote } from "../transcript/TranscriptNoteEditor";

vi.mock("@/lib/api", async (original) => ({
  ...await original<typeof import("@/lib/api")>(),
  updateTimelineNote: vi.fn(),
}));

const NOTE: TimelineNote = {
  id: "note-optimism", recording_id: "rec-optimism", kind: "head", body: "Original", revision: 2,
  created_at: "2026-10-27T10:00:00Z", updated_at: "2026-10-27T10:00:00Z",
};

/** The real editor consumes the optimistic notes exactly as both recording views do. */
function Notes() {
  const edits = useNoteEdits(NOTE.recording_id);
  const [editing, setEditing] = useState<EditingNote | null>(null);
  const notes = edits.apply([NOTE]);
  return (
    <>
      <p data-testid="shown-note">{notes[0].body}</p>
      <button onClick={(event) => setEditing({ noteId: NOTE.id, anchor: event.currentTarget })}>
        Edit note
      </button>
      <TranscriptNoteEditor editing={editing} notes={notes} onEditNote={edits.edit}
        onClose={() => setEditing(null)} />
    </>
  );
}

function pendingSave() {
  let reject!: (reason: unknown) => void;
  vi.mocked(updateTimelineNote).mockReturnValueOnce(new Promise<TimelineNote>((_resolve, fail) => { reject = fail; }));
  render(<Notes />);
  fireEvent.click(screen.getByRole("button", { name: "Edit note" }));
  const dialog = screen.getByRole("dialog");
  const textbox = within(dialog).getByRole("textbox");
  fireEvent.change(textbox, { target: { value: "My draft" } });
  fireEvent.click(within(dialog).getByRole("button", { name: "Save note" }));
  expect(screen.getByTestId("shown-note")).toHaveTextContent("My draft");
  expect(within(dialog).getByRole("button", { name: "Save note" })).toBeDisabled();
  return { dialog, textbox, reject };
}

afterEach(() => {
  forgetNoteOutboxes();
  vi.resetAllMocks();
});

describe("note editor dismissal during optimistic saves", () => {
  it("keeps the draft after outside presses while saving and after a refusal", async () => {
    const { dialog, textbox, reject } = pendingSave();
    fireEvent.pointerDown(document.body);
    expect(dialog).toBeInTheDocument();

    await act(async () => reject(new Error("offline")));
    expect(within(dialog).getByRole("alert")).toHaveTextContent("No answer from Nojoin. Try again.");
    expect(textbox).toHaveValue("My draft");
    expect(screen.getByTestId("shown-note")).toHaveTextContent("Original");
    fireEvent.pointerDown(document.body);
    expect(dialog).toBeInTheDocument();
  });

  it("wraps Tab within a draft while saving and after a refusal", async () => {
    const { dialog, textbox, reject } = pendingSave();
    const cancel = within(dialog).getByRole("button", { name: "Cancel" });
    cancel.focus();
    fireEvent.keyDown(cancel, { key: "Tab" });
    expect(dialog).toBeInTheDocument();
    expect(textbox).toHaveFocus();
    fireEvent.keyDown(textbox, { key: "Tab", shiftKey: true });
    expect(cancel).toHaveFocus();

    await act(async () => reject({ response: { status: 422, data: { detail: "Locked" } } }));
    const save = within(dialog).getByRole("button", { name: "Save note" });
    save.focus();
    fireEvent.keyDown(save, { key: "Tab" });
    expect(dialog).toBeInTheDocument();
    expect(textbox).toHaveFocus();
    expect(textbox).toHaveValue("My draft");
    expect(within(dialog).getByRole("alert")).toHaveTextContent("Locked");
  });

  it.each(["outside press", "Tab"])("still dismisses an unchanged editor with %s", (action) => {
    render(<Notes />);
    fireEvent.click(screen.getByRole("button", { name: "Edit note" }));
    const dialog = screen.getByRole("dialog");
    if (action === "outside press") fireEvent.pointerDown(document.body);
    else {
      const save = within(dialog).getByRole("button", { name: "Save note" });
      save.focus();
      fireEvent.keyDown(save, { key: "Tab" });
    }
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(updateTimelineNote).not.toHaveBeenCalled();
  });

  it.each(["Cancel", "Escape"])("permits deliberate %s while the draft saves", async (action) => {
    const { dialog, reject } = pendingSave();
    if (action === "Cancel") fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    else fireEvent.keyDown(dialog, { key: "Escape" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await act(async () => reject(new Error("offline")));
  });
});
