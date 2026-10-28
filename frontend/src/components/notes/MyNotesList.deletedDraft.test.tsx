import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { updateTimelineNote } from "@/lib/api";
import { forgetNoteOutboxes } from "@/lib/notes/noteOutboxes";
import { useNoteEdits } from "@/lib/notes/useNoteEdits";
import { resetSessionForTests, useSessionStore } from "@/lib/session";
import type { TimelineNote } from "@/types";

import TranscriptView from "../TranscriptView";
import { viewProps } from "../transcript/transcriptTestFixtures";

import MyNotesList from "./MyNotesList";

vi.mock("@/lib/api", async (original) => ({
  ...await original<typeof import("@/lib/api")>(),
  updateTimelineNote: vi.fn(),
}));

const NOTE: TimelineNote = {
  id: "deleted-draft-note", recording_id: "deleted-draft-recording", kind: "head", body: "Original", revision: 1,
  created_at: "2026-10-27T10:00:00Z", updated_at: "2026-10-27T10:00:00Z",
};

/** A remote listing can remove a note while its page-owned draft survives the list. */
type Surface = "list" | "transcript" | "live transcript";

function Page({ notes, visible = true, surface = "list" }: {
  notes: TimelineNote[];
  visible?: boolean;
  surface?: Surface;
}) {
  const edits = useNoteEdits(NOTE.recording_id);
  if (!visible) return null;
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
    expectLeaveProtection(true);
    fireEvent.click(within(editor).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expectLeaveProtection(false);
  },
);

it("exposes an absent note's retained draft after a layout remount and lets it be discarded", () => {
  const view = render(<Page notes={[NOTE]} />);
  typeDraft();
  view.rerender(<Page notes={[]} visible={false} />);
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expectLeaveProtection(true);

  view.rerender(<Page notes={[]} />);

  expect(screen.getByText("A thought to recover")).toBeVisible();
  expectLeaveProtection(true);
  fireEvent.click(screen.getByRole("button", { name: /Discard.*draft/i }));
  expect(screen.queryByText("A thought to recover")).not.toBeInTheDocument();
  expectLeaveProtection(false);
});

it("reopens an absent note's draft and keeps it accessible after a deleted-note save refusal", async () => {
  const view = render(<Page notes={[NOTE]} />);
  typeDraft();
  view.rerender(<Page notes={[]} visible={false} />);
  view.rerender(<Page notes={[]} />);
  fireEvent.click(screen.getByRole("button", { name: "Edit retained draft" }));
  const editor = screen.getByRole("dialog");
  expect(within(editor).getByRole("textbox")).toHaveValue("A thought to recover");
  expect(within(editor).getByText(/This note is no longer listed/)).toBeInTheDocument();
  vi.mocked(updateTimelineNote).mockRejectedValueOnce({ response: { status: 404 } });

  fireEvent.click(within(editor).getByRole("button", { name: "Save note" }));

  expect(await within(editor).findByRole("alert")).toHaveTextContent("This note was deleted elsewhere.");
  expect(within(editor).getByRole("textbox")).toHaveValue("A thought to recover");
  expect(updateTimelineNote).toHaveBeenCalledWith(NOTE.recording_id, NOTE.id, {
    expected_revision: 1, body: "A thought to recover",
  });
  expectLeaveProtection(true);
  fireEvent.click(within(editor).getByRole("button", { name: "Cancel" }));
  expect(screen.queryByRole("region", { name: "Unsaved note edits" })).not.toBeInTheDocument();
  expectLeaveProtection(false);
});
