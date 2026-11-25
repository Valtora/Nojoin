import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";
import type { ComponentProps } from "react";

import NotesView from "./NotesView";

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

function renderNotes(onNotesChange: (notes: string) => void, overrides: Partial<ComponentProps<typeof NotesView>> = {}) {
  return render(notesView(onNotesChange, overrides));
}

function notesView(onNotesChange: (notes: string) => void, overrides: Partial<ComponentProps<typeof NotesView>> = {}) {
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
    expect(onNotesChange).toHaveBeenCalledWith(`${NOTES}\n- Still editable`);
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

    expect(onNotesChange).toHaveBeenCalledWith(`${NOTES}\n- Second point`);
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
    expect(onNotesChange).toHaveBeenCalledWith(WITH_TABLE);
    const typed = WITH_TABLE.replace("Book the room", "Book the room for Friday");
    act(() => reportUpdate(typed));

    view.rerender(notesView(onNotesChange, { notes: WITH_TABLE }));

    expect(editorContent).toBe(typed);
  });

  it("still passes notes changed elsewhere to the editor", () => {
    const onNotesChange = vi.fn();
    const view = renderNotes(onNotesChange);
    act(() => {
      reportUpdate(WITH_TABLE);
      vi.advanceTimersByTime(1100);
    });

    view.rerender(notesView(onNotesChange, { notes: `${NOTES}\n- Added in another tab` }));

    expect(editorContent).toBe(`${NOTES}\n- Added in another tab`);
  });
});
