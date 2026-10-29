import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { RecordingStatus, type Recording } from "@/types";
import NotesSection from "./NotesSection";

vi.mock("@/components/NotesOutputsView", () => ({ default: (props: {
  notes: string | null; primaryError: string | null; allowCreateOutput: boolean;
  onGenerateNotes: () => void; onNotesChange: (notes: string) => void;
}) => <div>
  <p>{props.notes}</p>
  {props.primaryError && <p role="alert">{props.primaryError}</p>}
  <button onClick={props.onGenerateNotes}>Generate Notes</button>
  <button onClick={() => props.onNotesChange("Edited notes")}>Edit notes</button>
  {props.allowCreateOutput && <button>Generate another set</button>}
</div> }));

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
  ])("allows independent output actions in %s and preserves the finished stale banner", (status) => {
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
    expect(screen.getByText("Old generated notes")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Generate another set" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Regenerate" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Generate Notes" }));
    expect(onGenerateNotes).toHaveBeenCalledTimes(1);

    view.rerender(panel(false));
    fireEvent.click(screen.getByRole("button", { name: "Regenerate" }));
    expect(onGenerateNotes).toHaveBeenCalledTimes(2);
    expect(screen.getByText("Old generated notes")).toBeInTheDocument();
  });

  it("shows an existing primary generation error on the first read", () => {
    const recording = { id: "rec-1", status: RecordingStatus.UPLOADING,
      transcript: { notes: "Earlier notes", notes_status: "error", error_message: "Provider timed out" } } as Recording;
    render(<NotesSection active recording={recording} inFlight isGenerating={false}
      canUndo={false} canRedo={false} onNotesChange={vi.fn()} onGenerateNotes={vi.fn()}
      onFindAndReplace={vi.fn()} onUndo={vi.fn()} onRedo={vi.fn()} onExport={vi.fn()} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Provider timed out");
    expect(screen.getByText("Earlier notes")).toBeInTheDocument();
  });
});
