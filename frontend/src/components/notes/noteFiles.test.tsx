import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { forgetNoteOutboxes, noteOutboxFor } from "@/lib/notes/noteOutboxes";
import type { NoteEdits } from "@/lib/notes/useNoteEdits";
import { useNotificationStore } from "@/lib/notificationStore";
import { coded, NO_ANSWER, resetNotesServer, server } from "@/test/fakeNotesServer";
import type { NoteAttachment, NoteFileRemoval, TimelineNote } from "@/types";

const setNoteFileUseInNotes = vi.fn(async () => ({}));
const removeNoteFile = vi.fn<(recordingId: string, noteId: string) => Promise<NoteFileRemoval>>(
  async () => ({ note: null }),
);

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  ...(await import("@/test/fakeNotesServer")).fakeNotesApi,
  setNoteFileUseInNotes: (...args: unknown[]) => setNoteFileUseInNotes(...(args as [])),
  removeNoteFile: (recordingId: string, noteId: string) => removeNoteFile(recordingId, noteId),
}));

const { default: CapturesGallery } = await import("./CapturesGallery");
const { NoteFile, NoteFilesProvider, removalQuestion, useOpenNoteFile } = await import(
  "./NoteFiles"
);
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

const lastNotice = () => useNotificationStore.getState().activeNotifications.at(-1)?.message;

/** The viewer for one note, opened, with the page's refresh and Play. */
const openViewer = async (
  shown: TimelineNote,
  { onChanged = vi.fn(), onPlay }: { onChanged?: () => void; onPlay?: (s: number) => void } = {},
) => {
  const Opener = () => {
    const open = useOpenNoteFile();
    return <button onClick={() => open?.(shown.id)}>Open</button>;
  };
  render(
    <NoteFilesProvider
      recordingId={RECORDING}
      notes={[shown]}
      noteEdits={noteEdits}
      onChanged={onChanged}
      onPlay={onPlay}
    >
      <Opener />
    </NoteFilesProvider>,
  );
  fireEvent.click(screen.getByRole("button", { name: "Open" }));
  return screen.findByRole("dialog");
};

describe("a note's file", () => {
  beforeEach(() => {
    resetNotesServer();
    setNoteFileUseInNotes.mockClear();
    removeNoteFile.mockReset();
    removeNoteFile.mockResolvedValue({ note: null });
    useNotificationStore.setState({ activeNotifications: [] });
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

    expect(screen.getByTestId("captures-gallery")).toHaveAttribute("data-ui", "surface");
    expect(screen.getByTestId("captures-gallery")).toHaveAttribute("data-ui-surface", "pane");
    const items = screen.getAllByTestId("capture-item");
    expect(items.map((item) => within(item).getAllByRole("button")[1].textContent)).toEqual([
      "01:05",
      "01:30",
    ]);
    fireEvent.click(screen.getByRole("button", { name: "Open screen-capture.png, on the note at 01:05" }));
    const viewer = await screen.findByRole("dialog");
    const preview = within(viewer).getByRole("img").parentElement;
    expect(preview?.tagName).toBe("DIV");
    expect(preview).toHaveAttribute("data-ui", "surface");
    expect(preview).toHaveAttribute("data-ui-surface", "inset");
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

  it.each([
    { state: "loading", notes: null },
    { state: "empty", notes: [] },
  ])("keeps the gallery pane while its notes are $state", ({ notes }) => {
    render(
      <CapturesGallery
        recordingId={RECORDING}
        timelineNotes={notes}
        segments={[]}
        noteEdits={noteEdits}
      />,
    );
    const gallery = screen.getByTestId("captures-gallery");
    expect(gallery).toHaveAttribute("data-ui", "surface");
    expect(gallery).toHaveAttribute("data-ui-surface", "pane");
    expect(gallery).toHaveTextContent(notes === null ? "Loading captures…" : "No captures yet.");
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
    const preview = within(viewer).getByText("slides.zip", { selector: "span" }).parentElement;
    expect(preview?.tagName).toBe("P");
    expect(preview).toHaveAttribute("data-ui", "surface");
    expect(preview).toHaveAttribute("data-ui-surface", "inset");
    expect(within(viewer).getByRole("button", { name: "Download" })).toBeEnabled();
  });

  it("removes a captioned note's file after asking, and keeps the note", async () => {
    const onChanged = vi.fn();
    const captioned = { ...note("slide", 65_000, file()), body: "Budget slide" };
    removeNoteFile.mockResolvedValue({ note: { ...captioned, attachment: null } });
    const listAgain = vi.fn();
    noteOutboxFor(RECORDING).subscribeWrites(listAgain);
    const viewer = await openViewer(captioned, { onChanged });

    fireEvent.click(within(viewer).getByRole("button", { name: "Remove" }));

    const question = within(viewer).getByRole("group", {
      name: "Remove this file? Its note at 01:05 stays, with its text. This cannot be undone.",
    });
    expect(within(question).getByRole("button", { name: "Cancel" })).toHaveFocus();
    expect(removeNoteFile).not.toHaveBeenCalled();
    fireEvent.click(within(question).getByRole("button", { name: "Remove file" }));

    await vi.waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(removeNoteFile).toHaveBeenCalledTimes(1);
    expect(removeNoteFile).toHaveBeenCalledWith(RECORDING, "slide");
    // The page reads the recording again, and a page that doesn't poll fetches
    // the list, so the note's line loses its file without a reload.
    expect(onChanged).toHaveBeenCalled();
    expect(listAgain).toHaveBeenCalled();
    expect(lastNotice()).toBe("File removed. Its note stays.");
  });

  it("says a capture nobody captioned goes with its file, and Cancel keeps both", async () => {
    const capture = {
      ...note("still", 65_000, file({ use_in_notes: true, status: "READY" })),
      body: "Screen capture",
    };
    const viewer = await openViewer(capture);

    fireEvent.click(within(viewer).getByRole("button", { name: "Remove" }));
    const question = within(viewer).getByTestId("note-file-remove-confirm");
    expect(question).toHaveTextContent(
      "Remove this file and its note? The note at 01:05 has only the automatic text “Screen capture”, so it goes too. The file is used in notes, so notes already written are marked to regenerate. This cannot be undone.",
    );
    fireEvent.click(within(question).getByRole("button", { name: "Cancel" }));

    expect(within(viewer).queryByTestId("note-file-remove-confirm")).toBeNull();
    expect(within(viewer).getByRole("button", { name: "Remove" })).toHaveFocus();
    expect(removeNoteFile).not.toHaveBeenCalled();

    fireEvent.click(within(viewer).getByRole("button", { name: "Remove" }));
    fireEvent.click(within(viewer).getByRole("button", { name: "Remove file and note" }));

    await vi.waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(lastNotice()).toBe("File and note removed.");
  });

  it("says a note whose text is still its file's name goes with it", async () => {
    const named = { ...note("named", 65_000, file()), body: "screen-capture.png" };
    const viewer = await openViewer(named);

    fireEvent.click(within(viewer).getByRole("button", { name: "Remove" }));

    expect(
      within(viewer).getByRole("group", {
        name: "Remove this file and its note? The note at 01:05 has only the file's name as its text, so it goes too. This cannot be undone.",
      }),
    ).toBeInTheDocument();
    fireEvent.click(within(viewer).getByRole("button", { name: "Remove file and note" }));

    await vi.waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(lastNotice()).toBe("File and note removed.");
  });

  it.each([
    ["Q3 Budget.XLSX", "head", "Remove this file and its note? The note on the meeting has only the file's name as its text, so it goes too."],
    ["q3 budget.xlsx", "head", "Remove this file? Its note on the meeting stays, with its text."],
    ["Q3 Budget", "head", "Remove this file? Its note on the meeting stays, with its text."],
    ["Q3 Budget.XLSX, for Friday", "head", "Remove this file? Its note on the meeting stays, with its text."],
    ["Screen capture", "head", "Remove this file? Its note on the meeting stays, with its text."],
  ] as const)("asks about a note reading %j by the exact file name", (body, kind, question) => {
    expect(removalQuestion({ kind, body }, { title: "Q3 Budget.XLSX", use_in_notes: false }, null)).toBe(
      `${question} This cannot be undone.`,
    );
  });

  it("counts a file already gone as removed, and keeps the viewer on any other failure", async () => {
    removeNoteFile.mockRejectedValueOnce(NO_ANSWER).mockRejectedValueOnce(coded(410, "note_deleted_elsewhere"));
    const onChanged = vi.fn();
    const viewer = await openViewer(note("gone", 65_000, file()), { onChanged });

    fireEvent.click(within(viewer).getByRole("button", { name: "Remove" }));
    fireEvent.click(within(viewer).getByRole("button", { name: "Remove file" }));
    await vi.waitFor(() => expect(lastNotice()).toBe("The file could not be removed."));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(onChanged).not.toHaveBeenCalled();

    fireEvent.click(within(viewer).getByRole("button", { name: "Remove file" }));

    await vi.waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(lastNotice()).toBe("This file was already removed.");
    expect(onChanged).toHaveBeenCalled();
  });

  it("forgets the file's upload held for its note, so Retry can't bring it back", async () => {
    const outbox = noteOutboxFor(RECORDING);
    server.uploadFailures.push(NO_ANSWER);
    outbox.submit(
      { kind: "point", body: "Budget slide", anchorMs: 65_000 },
      { file: new File([new Uint8Array(8)], "screen-capture.png", { type: "image/png" }) },
    );
    const [entry] = outbox.getSnapshot().entries;
    await vi.waitFor(() =>
      expect(outbox.getSnapshot().uploads.get(entry.id)?.status).toBe("failed"),
    );
    // Its answer was lost, but the file landed: the list shows it on its note.
    const landed = { ...note(entry.id, 65_000, file()), body: "Budget slide" };
    removeNoteFile.mockResolvedValue({ note: { ...landed, attachment: null } });
    const viewer = await openViewer(landed);

    fireEvent.click(within(viewer).getByRole("button", { name: "Remove" }));
    fireEvent.click(within(viewer).getByRole("button", { name: "Remove file" }));

    await vi.waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(outbox.getSnapshot().uploads.size).toBe(0);
    expect(outbox.hasUnsaved()).toBe(false);
  });
});
