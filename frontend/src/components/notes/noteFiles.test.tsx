import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { forgetNoteOutboxes, noteOutboxFor } from "@/lib/notes/noteOutboxes";
import type { NoteEdits } from "@/lib/notes/useNoteEdits";
import { NO_ANSWER, resetNotesServer, server } from "@/test/fakeNotesServer";
import type { NoteAttachment, TimelineNote } from "@/types";

const setNoteFileUseInNotes = vi.fn(async () => ({}));

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  ...(await import("@/test/fakeNotesServer")).fakeNotesApi,
  setNoteFileUseInNotes: (...args: unknown[]) => setNoteFileUseInNotes(...(args as [])),
}));

const { default: CapturesGallery } = await import("./CapturesGallery");
const { NoteFile, NoteFilesProvider, useOpenNoteFile } = await import("./NoteFiles");
const { default: RecordingUnsavedNotes } = await import("./RecordingUnsavedNotes");

const RECORDING = "rec-1";

const file = (overrides: Partial<NoteAttachment> = {}): NoteAttachment => ({
  id: 7,
  title: "screen-capture.png",
  file_type: "image/png",
  status: "PENDING",
  use_in_notes: false,
  readable: true,
  is_image: true,
  ...overrides,
});

const note = (id: string, anchorMs: number, attachment: NoteAttachment | null): TimelineNote => ({
  id,
  recording_id: RECORDING,
  kind: "point",
  body: `Note ${id}`,
  anchor_ms: anchorMs,
  revision: 1,
  attachment,
  created_at: "2026-10-24T10:00:00",
  updated_at: "2026-10-24T10:00:00",
});

const noteEdits = { apply: <N,>(notes: readonly N[]) => [...notes] } as unknown as NoteEdits;

describe("a note's file", () => {
  beforeEach(() => {
    resetNotesServer();
    setNoteFileUseInNotes.mockClear();
  });
  afterEach(() => forgetNoteOutboxes());

  it("opens the viewer from the Captures tab, whose switch turns it on for the notes", async () => {
    const onChanged = vi.fn();
    const notes = [
      note("late", 90_000, file({ id: 8, title: "plan.pdf", is_image: false })),
      note("plain", 30_000, null),
      note("early", 65_000, file()),
    ];
    render(
      <NoteFilesProvider
        recordingId={RECORDING}
        notes={notes}
        noteEdits={noteEdits}
        onChanged={onChanged}
      >
        <CapturesGallery
          recordingId={RECORDING}
          timelineNotes={notes}
          segments={[]}
          noteEdits={noteEdits}
        />
      </NoteFilesProvider>,
    );

    const items = screen.getAllByTestId("capture-item");
    expect(items.map((item) => within(item).getAllByRole("button")[1].textContent)).toEqual([
      "01:05",
      "01:30",
    ]);
    fireEvent.click(screen.getByRole("button", { name: "Open screen-capture.png, on the note at 01:05" }));
    const viewer = await screen.findByRole("dialog");
    expect(viewer).toHaveTextContent("Off: the AI notes, Meeting Chat and search leave it out.");
    fireEvent.click(within(viewer).getByRole("switch"));

    await vi.waitFor(() => expect(onChanged).toHaveBeenCalled());
    expect(setNoteFileUseInNotes).toHaveBeenCalledWith(RECORDING, "early", true);
  });

  it("keeps Retry and Discard for a failed upload after Stop", async () => {
    const outbox = noteOutboxFor(RECORDING);
    server.uploadFailures.push(NO_ANSWER);
    outbox.submit(
      { kind: "point", body: "Screen capture", anchorMs: 65_000 },
      { file: new File([new Uint8Array(8)], "screen-capture.png", { type: "image/png" }) },
    );
    render(<RecordingUnsavedNotes recordingId={RECORDING} readEndMs={() => 0} listUploads />);

    const row = await screen.findByTestId("unsaved-note-file");
    await vi.waitFor(() => expect(row).toHaveAttribute("data-upload-status", "failed"));
    expect(row).toHaveTextContent("File for the note at 01:05");
    fireEvent.click(within(row).getByRole("button", { name: "Discard screen-capture.png" }));

    expect(screen.queryByTestId("unsaved-note-file")).toBeNull();
    expect(outbox.hasUnsaved()).toBe(false);
  });

  it("shows its picture on the note's line, and nothing outside a recording page", () => {
    const { rerender } = render(<NoteFile noteId="n1" attachment={file({ use_in_notes: true })} />);
    expect(screen.queryByTestId("note-file")).toBeNull();

    rerender(
      <NoteFilesProvider recordingId={RECORDING} notes={[]} noteEdits={noteEdits}>
        <NoteFile noteId="n1" attachment={file({ use_in_notes: true })} />
      </NoteFilesProvider>,
    );

    const shown = screen.getByTestId("note-file");
    expect(shown.querySelector("img")?.getAttribute("src")).toContain("/documents/7/preview");
    expect(shown).toHaveTextContent("In notes");
  });

  it("shows a note as moved or edited on this page, and plays from where it is now", async () => {
    const onPlay = vi.fn();
    const listed = note("moved", 65_000, file());
    // The page's overlay: the note was moved to 01:30 and recaptioned here.
    const edits = {
      apply: <N,>(notes: readonly N[]) =>
        notes.map((n) => ({ ...n, anchor_ms: 90_000, body: "Budget slide" })),
    } as unknown as NoteEdits;
    const Opener = () => {
      const open = useOpenNoteFile();
      return <button onClick={() => open?.("moved")}>Open</button>;
    };
    render(
      <NoteFilesProvider recordingId={RECORDING} notes={[listed]} noteEdits={edits} onPlay={onPlay}>
        <Opener />
      </NoteFilesProvider>,
    );

    fireEvent.click(screen.getByRole("button", { name: "Open" }));
    const viewer = await screen.findByRole("dialog");
    expect(viewer).toHaveTextContent("Note at 01:30: Budget slide");
    fireEvent.click(within(viewer).getByRole("button", { name: "Play from 01:30" }));

    expect(onPlay).toHaveBeenCalledWith(90);
  });

  it("keeps a file Nojoin can't read to download, its switch off with the reason", async () => {
    const Opener = () => {
      const open = useOpenNoteFile();
      return <button onClick={() => open?.("zip")}>Open</button>;
    };
    const archive = file({ title: "slides.zip", readable: false, is_image: false });
    render(
      <NoteFilesProvider recordingId={RECORDING} notes={[note("zip", 5_000, archive)]} noteEdits={noteEdits}>
        <Opener />
      </NoteFilesProvider>,
    );

    fireEvent.click(screen.getByRole("button", { name: "Open" }));
    const viewer = await screen.findByRole("dialog");

    expect(within(viewer).getByRole("switch")).toBeDisabled();
    expect(viewer).toHaveTextContent("Nojoin can't read this kind of file");
    expect(viewer.querySelector("img")).toBeNull();
    expect(within(viewer).getByRole("button", { name: "Download" })).toBeEnabled();
  });
});
