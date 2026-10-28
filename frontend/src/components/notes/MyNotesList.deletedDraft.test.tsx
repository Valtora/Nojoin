import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { forgetNoteOutboxes } from "@/lib/notes/noteOutboxes";
import { useNoteEdits } from "@/lib/notes/useNoteEdits";
import { resetSessionForTests, useSessionStore } from "@/lib/session";
import type { TimelineNote } from "@/types";

import TranscriptView from "../TranscriptView";
import { viewProps } from "../transcript/transcriptTestFixtures";

import MyNotesList from "./MyNotesList";

const NOTE: TimelineNote = {
  id: "deleted-draft-note", recording_id: "deleted-draft-recording", kind: "head", body: "Original", revision: 1,
  created_at: "2026-10-27T10:00:00Z", updated_at: "2026-10-27T10:00:00Z",
};

/** A remote listing can remove a note while its page-owned draft survives the list. */
type Surface = "list" | "transcript" | "live transcript";

function Page({ notes, surface = "list" }: {
  notes: TimelineNote[];
  surface?: Surface;
}) {
  const edits = useNoteEdits(NOTE.recording_id);
  if (surface === "list") return <MyNotesList recordingId={NOTE.recording_id} timelineNotes={notes}
    noteEdits={edits} segments={[]} onPlay={() => {}} />;
  return <TranscriptView {...viewProps([])} recordingId={NOTE.recording_id} timelineNotes={edits.apply(notes)}
    onEditNote={edits.edit} noteDrafts={edits.drafts}
    live={surface === "live transcript" ? { hasLoaded: true } : undefined} />;
}

function typeDraft() {
  fireEvent.click(screen.getByRole("button", { name: /^Edit note/ }));
  fireEvent.change(within(screen.getByRole("dialog")).getByRole("textbox"), {
    target: { value: "A thought to recover" },
  });
}

function expectLeaveProtection(expected: boolean) {
  const event = new Event("beforeunload", { cancelable: true });
  window.dispatchEvent(event);
  expect(event.defaultPrevented).toBe(expected);
  expect(useSessionStore.getState().heldBy.includes("notes")).toBe(expected);
}

afterEach(() => {
  cleanup();
  forgetNoteOutboxes();
  resetSessionForTests();
  vi.resetAllMocks();
});

it.each(["list", "transcript", "live transcript"] as const)(
  "keeps a dirty %s editor accessible when a remote listing removes its note, until Cancel",
  (surface) => {
    const view = render(<Page notes={[NOTE]} surface={surface} />);
    typeDraft();
    expectLeaveProtection(true);

    view.rerender(<Page notes={[]} surface={surface} />);

    const editor = screen.getByRole("dialog");
    expect(within(editor).getByRole("textbox")).toHaveValue("A thought to recover");
    expect(within(editor).getByText(/This note is no longer listed/)).toBeInTheDocument();
    expectLeaveProtection(true);
    fireEvent.click(within(editor).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expectLeaveProtection(false);
  },
);
