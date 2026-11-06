import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";
import type { ComponentProps } from "react";

import NotesView from "./NotesView";
import { renderCounter } from "@/test/renderCounts";

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

function renderNotes(onNotesChange: (notes: string) => void, overrides: Partial<ComponentProps<typeof NotesView>> = {}) {
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
      {...overrides}
    />,
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
});

describe("NotesView while notes generate", () => {
  it("shows the step a long meeting's notes are on in the generate button", () => {
    const step = "Combining notes, parts 1 to 3 of 9 (retrying a shorter answer)";
    renderNotes(vi.fn(), { isGenerating: true, generatingStep: step });

    const button = screen.getByRole("button", { name: step });
    // Cut short in the button when it does not fit, whole in the tooltip.
    expect(button).toHaveAttribute("title", step);
    expect(screen.queryByText("Generating...")).not.toBeInTheDocument();
    // Read out as a status, which is not drawn as a line of its own.
    expect(screen.getByRole("status")).toHaveTextContent(step);
    expect(screen.getByRole("status")).toHaveClass("sr-only");
  });

  it("reads Generating... until a step arrives", () => {
    renderNotes(vi.fn(), { isGenerating: true, generatingStep: null });

    expect(screen.getByRole("button", { name: "Generating..." })).toHaveAttribute("title", "Generating...");
    expect(screen.getByRole("status")).toHaveTextContent("");
  });

  it("redraws the generate button for a new step, and not for the rest of the toolbar", async () => {
    const props = {
      recordingId: "r1",
      notes: NOTES,
      onNotesChange: vi.fn(),
      onGenerateNotes: vi.fn(),
      onFindAndReplace: vi.fn(),
      onUndo: vi.fn(),
      onRedo: vi.fn(),
      canRedo: false,
      isGenerating: true,
      onExport: vi.fn(),
    };
    const view = render(<NotesView {...props} canUndo={false} generatingStep="Drafting notes, part 1 of 5" />);
    const counter = await renderCounter();
    try {
      await counter.take();
      view.rerender(<NotesView {...props} canUndo generatingStep="Drafting notes, part 1 of 5" />);
      const unrelated = await counter.take();
      view.rerender(<NotesView {...props} canUndo generatingStep="Drafting notes, part 2 of 5" />);
      const stepped = await counter.take();

      expect(unrelated("components/GenerateNotesButton.tsx", "GenerateNotesButton")).toBe(0);
      expect(stepped("components/GenerateNotesButton.tsx", "GenerateNotesButton")).toBe(1);
      expect(screen.getByRole("button", { name: "Drafting notes, part 2 of 5" })).toBeInTheDocument();
    } finally {
      await counter.stop();
    }
  });

  it("drops a step left over once the notes are no longer generating", () => {
    renderNotes(vi.fn(), { isGenerating: false, generatingStep: "Combining notes" });

    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });
});
