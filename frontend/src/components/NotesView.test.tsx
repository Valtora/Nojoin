import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { act, render } from "@testing-library/react";

import NotesView from "./NotesView";

vi.mock("@/lib/api", () => ({
  getSettings: vi.fn().mockResolvedValue({}),
  listNotesTemplates: vi.fn().mockResolvedValue([]),
}));

// The editor is stubbed so a test can report any content as an editor update.
let reportUpdate: (content: string) => void = () => {};
vi.mock("./RichTextEditor", () => ({
  default: ({ onChange }: { onChange: (content: string) => void }) => {
    reportUpdate = onChange;
    return null;
  },
}));

const NOTES = "## Summary\n\n- First point";

function renderNotes(onNotesChange: (notes: string) => void) {
  return render(
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
    />,
  );
}

describe("NotesView autosave", () => {
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
});
