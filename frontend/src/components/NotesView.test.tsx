import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";
import type { ComponentProps } from "react";

import NotesView from "./NotesView";
import type { NotesConflictChoice, NotesSaveOutcome } from "@/lib/notesSave";

vi.mock("@/lib/api", () => ({
  getSettings: vi.fn().mockResolvedValue({}),
  listNotesTemplates: vi.fn().mockResolvedValue([]),
}));

// The editor is stubbed so a test can report any content as an editor update,
// and read the content NotesView last gave it.
let reportUpdate: (content: string) => void = () => {};
let editorContent: string | null = null;
vi.mock("./RichTextEditor", () => ({
  default: ({ content, onChange }: { content: string; onChange: (content: string) => void }) => {
    reportUpdate = onChange;
    editorContent = content;
    return null;
  },
}));

const NOTES = "## Summary\n\n- First point";

type OnNotesChange = ComponentProps<typeof NotesView>["onNotesChange"];

function renderNotes(onNotesChange: OnNotesChange, overrides: Partial<ComponentProps<typeof NotesView>> = {}) {
  return render(notesView(onNotesChange, overrides));
}

function notesView(onNotesChange: OnNotesChange, overrides: Partial<ComponentProps<typeof NotesView>> = {}) {
  return (
    <NotesView
      recordingId="r1"
      notes={NOTES}
      onNotesChange={onNotesChange}
      onGenerateNotes={vi.fn()}
      onFindAndReplace={vi.fn()}
      onUndo={vi.fn()}
      onRedo={vi.fn()}
      canUndo={false}
      canRedo={false}
      isGenerating={false}
      onExport={vi.fn()}
      {...overrides}
    />
  );
}

describe("NotesView autosave", () => {
  it("disables notes export independently of autosaving edits", () => {
    const onExport = vi.fn();
    const onNotesChange = vi.fn();
    renderNotes(onNotesChange, { disableExport: true, onExport });
    expect(screen.getByTitle("Export Notes")).toBeDisabled();
    fireEvent.click(screen.getByTitle("Export Notes"));
    expect(onExport).not.toHaveBeenCalled();
    act(() => {
      reportUpdate(`${NOTES}\n- Still editable`);
      vi.advanceTimersByTime(1100);
    });
    expect(onNotesChange).toHaveBeenCalledWith(`${NOTES}\n- Still editable`, expect.any(Function));
  });

  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("does not save an update that leaves the notes as saved", () => {
    const onNotesChange = vi.fn();
    renderNotes(onNotesChange);

    act(() => {
      reportUpdate(NOTES);
      vi.advanceTimersByTime(2000);
    });

    expect(onNotesChange).not.toHaveBeenCalled();
  });

  it("saves an edit once the debounce passes", () => {
    const onNotesChange = vi.fn();
    renderNotes(onNotesChange);

    act(() => {
      reportUpdate(`${NOTES}\n- Second point`);
      vi.advanceTimersByTime(1100);
    });

    expect(onNotesChange).toHaveBeenCalledWith(`${NOTES}\n- Second point`, expect.any(Function));
  });

  it("drops a pending edit that is undone before it saves", () => {
    const onNotesChange = vi.fn();
    renderNotes(onNotesChange);

    act(() => {
      reportUpdate(`${NOTES}\n- Typo`);
      vi.advanceTimersByTime(500);
      reportUpdate(NOTES);
      vi.advanceTimersByTime(2000);
    });

    expect(onNotesChange).not.toHaveBeenCalled();
  });

  // The editor's Markdown ends in a newline when the notes end with a table,
  // and the saved notes come back through the notes prop trimmed (#1074).
  const WITH_TABLE = `${NOTES}\n\n| Owner | Task |\n| --- | --- |\n| Alice Example | Book the room |\n`;

  it("keeps what was typed while a save was in flight when that save comes back", () => {
    const onNotesChange = vi.fn();
    const view = renderNotes(onNotesChange);

    act(() => {
      reportUpdate(WITH_TABLE);
      vi.advanceTimersByTime(1100);
    });
    expect(onNotesChange).toHaveBeenCalledWith(WITH_TABLE, expect.any(Function));
    const typed = WITH_TABLE.replace("Book the room", "Book the room for Friday");
    act(() => reportUpdate(typed));

    view.rerender(notesView(onNotesChange, { notes: WITH_TABLE }));

    expect(editorContent).toBe(typed);
  });

  it("still passes notes changed elsewhere to the editor", async () => {
    const onNotesChange = vi.fn();
    const view = renderNotes(onNotesChange);
    act(() => {
      reportUpdate(WITH_TABLE);
      vi.advanceTimersByTime(1100);
    });
    // The save has come back.
    await act(async () => {});

    view.rerender(notesView(onNotesChange, { notes: `${NOTES}\n- Added in another tab` }));

    expect(editorContent).toBe(`${NOTES}\n- Added in another tab`);
  });
});

describe("NotesView with someone else editing the same notes", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  const OTHERS = `${NOTES}\n- Bob's line`;
  const MINE = `${NOTES}\n- My line`;

  it("saves from the revision the text was edited from, not one a poll brought in meanwhile", async () => {
    let finishSave: (outcome: NotesSaveOutcome) => void = () => undefined;
    const bases: (string | undefined)[] = [];
    const onNotesChange = vi.fn((_notes: string, baseRevision: () => string | undefined) => {
      bases.push(baseRevision());
      return new Promise<NotesSaveOutcome>((resolve) => {
        finishSave = resolve;
      });
    });
    const view = renderNotes(onNotesChange, { notesRevision: "rev-a" });

    act(() => reportUpdate(MINE));
    // Bob's save reaches this tab's poll while the edit waits to be saved.
    view.rerender(notesView(onNotesChange, { notes: OTHERS, notesRevision: "rev-bob" }));
    expect(editorContent).toBe(MINE);

    act(() => {
      vi.advanceTimersByTime(1100);
    });
    expect(bases).toEqual(["rev-a"]);
    await act(async () => finishSave({ status: "saved", revision: "rev-mine" }));
  });

  it("holds the refused text until the user picks their version or the other one", async () => {
    let choose: (choice: NotesConflictChoice) => void = () => undefined;
    const onNotesChange = vi.fn(
      async (): Promise<NotesSaveOutcome> => ({
        status: "conflict",
        resolution: new Promise<NotesConflictChoice>((resolve) => {
          choose = resolve;
        }),
      }),
    );
    const view = renderNotes(onNotesChange, { notesRevision: "rev-a" });

    act(() => {
      reportUpdate(MINE);
      vi.advanceTimersByTime(1100);
    });
    await act(async () => {});
    view.rerender(notesView(onNotesChange, { notes: OTHERS, notesRevision: "rev-bob" }));
    expect(editorContent).toBe(MINE);

    await act(async () => choose({ choice: "theirs" }));
    expect(editorContent).toBe(OTHERS);
  });
});
