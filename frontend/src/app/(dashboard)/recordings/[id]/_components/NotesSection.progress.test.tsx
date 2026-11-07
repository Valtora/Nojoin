import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { RecordingStatus, type Recording } from "@/types";
import NotesSection from "./NotesSection";

vi.mock("@/lib/api", () => ({
  getSettings: vi.fn().mockResolvedValue({}),
  listNotesTemplates: vi.fn().mockResolvedValue([]),
}));
vi.mock("@/components/RichTextEditor", () => ({ default: () => null }));

describe("the notes tab while a long meeting's notes generate", () => {
  it("shows the step the recording's notes are on", () => {
    const recording = {
      id: "rec-1",
      status: RecordingStatus.PROCESSED,
      transcript: {
        notes: "Earlier notes",
        notes_status: "generating",
        notes_progress: "Drafting notes, part 2 of 5",
      },
    } as Recording;

    render(<NotesSection active recording={recording} isGenerating canUndo={false} canRedo={false}
      onNotesChange={vi.fn()} onGenerateNotes={vi.fn()} onFindAndReplace={vi.fn()}
      onUndo={vi.fn()} onRedo={vi.fn()} onExport={vi.fn()} />);

    expect(screen.getByRole("button", { name: "Drafting notes, part 2 of 5" })).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Drafting notes, part 2 of 5");
  });
});
