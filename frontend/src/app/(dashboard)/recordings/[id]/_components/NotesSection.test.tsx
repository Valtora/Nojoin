import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { RecordingStatus, type Recording } from "@/types";
import NotesSection from "./NotesSection";

vi.mock("@/components/NotesOutputsView", () => ({ default: () => <div>Finished notes editor</div> }));

describe("generated notes eligibility", () => {
  it("keeps retained generated notes readable without editing or regeneration during processing", async () => {
    const onNotesChange = vi.fn();
    const onGenerateNotes = vi.fn().mockResolvedValue(undefined);
    const recording = { id: "rec-1", status: RecordingStatus.PROCESSING,
      transcript: { notes: "Old generated notes", notes_stale_documents: true } } as Recording;
    render(<NotesSection active recording={recording} inFlight hasRetainedPublication
      isGenerating={false} canUndo={false} canRedo={false} onNotesChange={onNotesChange}
      onGenerateNotes={onGenerateNotes} onFindAndReplace={vi.fn()} onUndo={vi.fn()} onRedo={vi.fn()} onExport={vi.fn()} />);
    await waitFor(() => expect(screen.getByText("Old generated notes")).toBeInTheDocument());
    expect(screen.getByText("Old generated notes").closest("[contenteditable]")).toHaveAttribute("contenteditable", "false");
    expect(screen.queryByRole("button", { name: /Regenerate/ })).not.toBeInTheDocument();
    expect(onNotesChange).not.toHaveBeenCalled();
    expect(onGenerateNotes).not.toHaveBeenCalled();
  });

  it.each([
    RecordingStatus.PAUSED,
    RecordingStatus.UPLOADING,
    RecordingStatus.QUEUED,
    RecordingStatus.PROCESSING,
  ])("keeps stale-note regeneration unavailable in %s until processing finishes", (status) => {
    const recording = {
      id: "rec-1", status,
      transcript: { notes: "Old generated notes", notes_stale_documents: true },
    } as Recording;
    const onGenerateNotes = vi.fn().mockResolvedValue(undefined);
    const panel = (inFlight: boolean) => (
      <NotesSection active recording={{ ...recording, status: inFlight ? status : RecordingStatus.PROCESSED }}
        inFlight={inFlight} canExport={!inFlight} isGenerating={false} canUndo={false} canRedo={false}
        onNotesChange={vi.fn()} onGenerateNotes={onGenerateNotes} onFindAndReplace={vi.fn()}
        onUndo={vi.fn()} onRedo={vi.fn()} onExport={vi.fn()} />
    );
    const view = render(panel(true));
    expect(screen.getByText(/Meeting notes will be available once processing finishes/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Regenerate" })).not.toBeInTheDocument();
    expect(screen.queryByText("Finished notes editor")).not.toBeInTheDocument();
    expect(onGenerateNotes).not.toHaveBeenCalled();

    view.rerender(panel(false));
    fireEvent.click(screen.getByRole("button", { name: "Regenerate" }));
    expect(onGenerateNotes).toHaveBeenCalledTimes(1);
    expect(screen.getByText("Finished notes editor")).toBeInTheDocument();
  });
});
