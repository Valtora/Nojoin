import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { NotesOutput } from "@/types";
import type { NotesViewProps } from "./NotesView";
import NotesOutputsView from "./NotesOutputsView";

const api = vi.hoisted(() => ({
  listNotesOutputs: vi.fn(), generateNotes: vi.fn(), deleteNotesOutput: vi.fn(),
  updateNotesOutput: vi.fn(), exportContent: vi.fn(), listNotesTemplates: vi.fn(),
}));
vi.mock("@/lib/api", () => api);
vi.mock("./NotesView", () => ({ default: (props: NotesViewProps) => <div>
  <p>{props.notes}</p>
  <button disabled={props.isGenerating} onClick={() => void props.onGenerateNotes()}>Regenerate Notes</button>
  <button onClick={() => props.onNotesChange("Edited extra")}>Edit notes</button>
  <button onClick={props.onExport}>Export Notes</button>
  {props.toolbarActions}
</div> }));

const extra: NotesOutput = {
  id: 11, name: "Task list", notes: "Extra tasks", notes_template_id: 7,
  notes_template_sections: "## Tasks", notes_status: "completed", error_message: null,
  created_at: "2026-10-29", updated_at: "2026-10-29",
};
const props = (): NotesViewProps => ({
  recordingId: "rec-1", notes: "First notes", onNotesChange: vi.fn(),
  onGenerateNotes: vi.fn().mockResolvedValue(undefined), onFindAndReplace: vi.fn(),
  onUndo: vi.fn(), onRedo: vi.fn(), canUndo: false, canRedo: false,
  isGenerating: false, onExport: vi.fn(),
});

beforeEach(() => {
  vi.resetAllMocks();
  api.listNotesOutputs.mockResolvedValue([]);
  api.listNotesTemplates.mockResolvedValue({
    templates: [{ id: 7, name: "Task list", description: null }],
    builtin: { name: "Nojoin default", description: null },
  });
  api.generateNotes.mockResolvedValue({ output: extra });
  api.deleteNotesOutput.mockResolvedValue(undefined);
  api.updateNotesOutput.mockResolvedValue({ ...extra, notes: "Edited extra" });
  api.exportContent.mockResolvedValue(undefined);
});

describe("notes outputs", () => {
  it("creates, regenerates and deletes an extra set, returning to a single view", async () => {
    const primary = props();
    render(<NotesOutputsView {...primary} />);
    await waitFor(() => expect(api.listNotesOutputs).toHaveBeenCalled());
    expect(screen.queryByRole("tablist")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Generate another set" }));
    fireEvent.click(await screen.findByRole("button", { name: "Task list" }));
    await screen.findByRole("tab", { name: "Task list", selected: true });
    expect(screen.getByText("Extra tasks")).toBeInTheDocument();
    expect(api.generateNotes).toHaveBeenCalledWith("rec-1", 7, { create_output: true });
    fireEvent.click(screen.getByRole("button", { name: "Regenerate Notes" }));
    await waitFor(() => expect(api.generateNotes).toHaveBeenCalledWith("rec-1", null, { output_id: 11 }));
    expect(primary.onGenerateNotes).not.toHaveBeenCalled();
    await waitFor(() => expect(screen.getByRole("button", { name: "Delete set" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "Delete set" }));
    const dialog = await screen.findByRole("dialog");
    expect(api.deleteNotesOutput).not.toHaveBeenCalled();
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(screen.getByText("Extra tasks")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Delete set" }));
    fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Delete set" }));
    await waitFor(() => expect(screen.queryByRole("tablist")).not.toBeInTheDocument());
    expect(screen.getByText("First notes")).toBeInTheDocument();
    expect(api.deleteNotesOutput).toHaveBeenCalledWith("rec-1", 11);
  });

  it("saves and exports only the selected extra, then returns to the primary actions", async () => {
    api.listNotesOutputs.mockResolvedValue([extra]);
    const primary = props();
    render(<NotesOutputsView {...primary} />);
    fireEvent.click(await screen.findByRole("tab", { name: "Task list" }));
    fireEvent.click(screen.getByRole("button", { name: "Edit notes" }));
    await screen.findByText("Edited extra");
    expect(api.updateNotesOutput).toHaveBeenCalledWith("rec-1", 11, "Edited extra");
    expect(primary.onNotesChange).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Export Notes" }));
    await waitFor(() => expect(api.exportContent).toHaveBeenCalledWith("rec-1", ["notes"], "txt", {}, true, 11));
    expect(primary.onExport).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("tab", { name: "Meeting notes" }));
    fireEvent.click(screen.getByRole("button", { name: "Export Notes" }));
    expect(primary.onExport).toHaveBeenCalledOnce();
  });

  it("shows generation errors without losing the original notes", async () => {
    api.generateNotes.mockRejectedValue({ response: { data: { detail: "This recording is being reprocessed." } } });
    render(<NotesOutputsView {...props()} />);
    fireEvent.click(screen.getByRole("button", { name: "Generate another set" }));
    fireEvent.click(await screen.findByRole("button", { name: "Task list" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("This recording is being reprocessed.");
    expect(screen.getByText("First notes")).toBeInTheDocument();
    expect(screen.queryByRole("tablist")).not.toBeInTheDocument();
  });

  it("supports keyboard navigation and keeps an extra's generation state on its own tab", async () => {
    api.listNotesOutputs.mockResolvedValue([{ ...extra, notes_status: "generating" }]);
    render(<NotesOutputsView {...props()} />);
    const primary = await screen.findByRole("tab", { name: "Meeting notes" });
    expect(screen.getByRole("button", { name: "Regenerate Notes" })).toBeEnabled();
    fireEvent.keyDown(primary, { key: "ArrowRight" });
    const tasks = screen.getByRole("tab", { name: "Task list", selected: true });
    expect(tasks).toHaveFocus();
    expect(screen.getByRole("button", { name: "Regenerate Notes" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Delete set" })).toBeDisabled();
    fireEvent.keyDown(tasks, { key: "Home" });
    expect(primary).toHaveAttribute("aria-selected", "true");
    expect(primary).toHaveFocus();
  });
});
