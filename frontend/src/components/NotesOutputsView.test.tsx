import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { NotesOutput } from "@/types";
import type { NotesViewProps } from "./NotesView";
import NotesOutputsView from "./NotesOutputsView";

const api = vi.hoisted(() => ({
  listNotesOutputs: vi.fn(), generateNotes: vi.fn(), deleteNotesOutput: vi.fn(),
  updateNotesOutput: vi.fn(), exportContent: vi.fn(), listNotesTemplates: vi.fn(),
  getSettings: vi.fn(), updateSettings: vi.fn(),
}));
vi.mock("@/lib/api", () => api);
vi.mock("./NotesView", () => ({ default: (props: NotesViewProps) => <div>
  <p>{props.notes}</p>
  <button disabled={props.isGenerating} onClick={() => void props.onGenerateNotes()}>Regenerate Notes</button>
  <button onClick={() => props.onNotesChange("Edited extra")}>Edit notes</button>
  <button onClick={props.onExport}>Export Notes</button>
  <button onClick={() => void props.onFindAndReplace("tasks", "jobs")}>Replace all</button>
  {props.toolbarActions}
</div> }));

const extra: NotesOutput = {
  id: 11, kind: "notes", name: "Task list", notes: "Extra tasks", notes_template_id: 7,
  notes_template_sections: "## Tasks", notes_status: "completed", error_message: null,
  created_at: "2026-10-29", updated_at: "2026-10-29",
};
// What POST /notes/generate answers for an extra set: the run has only started.
const started = (output: NotesOutput) => ({
  status: "success", notes_status: "generating", error_message: null, message: "Note generation started",
  output: { ...output, notes_status: "generating" },
});
const props = (): NotesViewProps => ({
  recordingId: "rec-1", notes: "First notes", onNotesChange: vi.fn(),
  onGenerateNotes: vi.fn().mockResolvedValue(undefined), onFindAndReplace: vi.fn().mockResolvedValue(undefined),
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
  api.generateNotes.mockResolvedValue(started({ ...extra, notes: null }));
  api.deleteNotesOutput.mockResolvedValue(undefined);
  api.updateNotesOutput.mockResolvedValue({ ...extra, notes: "Edited extra" });
  api.exportContent.mockResolvedValue(undefined);
  api.getSettings.mockResolvedValue({ glossary_terms: "" });
  api.updateSettings.mockResolvedValue({ glossary_terms: "" });
});

afterEach(() => { vi.useRealTimers(); });

describe("notes outputs", () => {
  it("creates a glossary checklist, extracts more into it and regenerates it", async () => {
    const glossary = { ...extra, kind: "glossary", name: "Glossary suggestions", notes: "ARR: annual recurring revenue", notes_template_id: null };
    api.generateNotes.mockResolvedValue({ output: glossary });
    render(<NotesOutputsView {...props()} />);
    await waitFor(() => expect(api.listNotesOutputs).toHaveBeenCalled());
    fireEvent.click(screen.getByRole("button", { name: "Generate another set" }));
    fireEvent.click(await screen.findByRole("button", { name: /Glossary suggestions/ }));
    await screen.findByRole("tab", { name: "Glossary suggestions", selected: true });
    expect(api.generateNotes).toHaveBeenCalledWith("rec-1", null, { create_output: true, kind: "glossary" });
    expect(screen.getByRole("checkbox", { name: "ARR: annual recurring revenue" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Edit notes" })).not.toBeInTheDocument();

    api.generateNotes.mockResolvedValue({ output: { ...glossary, notes: "ARR: annual recurring revenue\nKafka: event streaming" } });
    fireEvent.click(screen.getByRole("button", { name: "Extract more" }));
    await screen.findByRole("checkbox", { name: "Kafka: event streaming" });
    expect(api.generateNotes).toHaveBeenLastCalledWith("rec-1", null, { output_id: 11, extract_more: true });

    api.generateNotes.mockResolvedValue({ output: { ...glossary, notes: "New: replacement set" } });
    await waitFor(() => expect(screen.getByRole("button", { name: "Regenerate suggestions" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "Regenerate suggestions" }));
    await screen.findByRole("checkbox", { name: "New: replacement set" });
    expect(screen.queryByRole("checkbox", { name: "Kafka: event streaming" })).not.toBeInTheDocument();
    expect(api.generateNotes).toHaveBeenLastCalledWith("rec-1", null, { output_id: 11 });
  });

  it("discovers automatically created outputs while primary notes generate", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    api.listNotesOutputs.mockResolvedValueOnce([]).mockResolvedValue([
      { ...extra, kind: "glossary", name: "Glossary suggestions", notes: "ARR: recurring revenue" },
    ]);
    render(<NotesOutputsView {...props()} isGenerating />);
    await waitFor(() => expect(api.listNotesOutputs).toHaveBeenCalledOnce());
    await act(() => vi.advanceTimersByTimeAsync(3000));
    expect(await screen.findByRole("tab", { name: "Glossary suggestions" })).toBeInTheDocument();
  });

  it("creates, regenerates and deletes an extra set, returning to a single view", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    api.listNotesOutputs.mockResolvedValueOnce([]).mockResolvedValue([extra]);
    api.generateNotes.mockResolvedValueOnce(started({ ...extra, notes: null })).mockResolvedValueOnce(started(extra));
    const primary = props();
    render(<NotesOutputsView {...primary} />);
    await waitFor(() => expect(api.listNotesOutputs).toHaveBeenCalled());
    expect(screen.queryByRole("tablist")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Generate another set" }));
    fireEvent.click(await screen.findByRole("button", { name: "Task list" }));
    await screen.findByRole("tab", { name: "Task list", selected: true });
    expect(api.generateNotes).toHaveBeenCalledWith("rec-1", 7, { create_output: true });
    expect(screen.getByRole("button", { name: "Regenerate Notes" })).toBeDisabled();
    expect(screen.queryByText("Extra tasks")).not.toBeInTheDocument();
    await act(() => vi.advanceTimersByTimeAsync(3000));
    expect(screen.getByText("Extra tasks")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Regenerate Notes" }));
    await waitFor(() => expect(api.generateNotes).toHaveBeenCalledWith("rec-1", null, { output_id: 11 }));
    expect(primary.onGenerateNotes).not.toHaveBeenCalled();
    // Once the request settles, the set's own generating state keeps it locked.
    await act(async () => {});
    expect(screen.getByRole("button", { name: "Regenerate Notes" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Delete set" })).toBeDisabled();
    expect(screen.getByText("Extra tasks")).toBeInTheDocument();
    await act(() => vi.advanceTimersByTimeAsync(3000));
    expect(screen.getByRole("button", { name: "Delete set" })).toBeEnabled();
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
    const reprocessing = "This recording is being reprocessed. Try again once processing has finished.";
    api.generateNotes.mockRejectedValue({ response: { data: { detail: reprocessing } } });
    render(<NotesOutputsView {...props()} />);
    fireEvent.click(screen.getByRole("button", { name: "Generate another set" }));
    fireEvent.click(await screen.findByRole("button", { name: "Task list" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(reprocessing);
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

  it("polls only while a set generates, leaving the other sets as they are", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const other = { ...extra, id: 12, notes: "Other set" };
    api.listNotesOutputs
      .mockResolvedValueOnce([{ ...extra, notes: null, notes_status: "generating" }, other])
      .mockResolvedValue([extra, { ...other, notes: "Older server copy" }]);
    render(<NotesOutputsView {...props()} />);
    fireEvent.click(await screen.findByRole("tab", { name: "Task list 2" }));
    expect(screen.getByText("Other set")).toBeInTheDocument();
    await act(() => vi.advanceTimersByTimeAsync(3000));
    expect(api.listNotesOutputs).toHaveBeenCalledTimes(2);
    expect(screen.getByText("Other set")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "Task list" }));
    expect(screen.getByText("Extra tasks")).toBeInTheDocument();
    await act(() => vi.advanceTimersByTimeAsync(9000));
    expect(api.listNotesOutputs).toHaveBeenCalledTimes(2);
  });

  it("shows no alert when the sets cannot be loaded", async () => {
    const logged = vi.spyOn(console, "error").mockImplementation(() => undefined);
    api.listNotesOutputs.mockRejectedValue(new Error("Not available in the demo"));
    render(<NotesOutputsView {...props()} />);
    await waitFor(() => expect(logged).toHaveBeenCalled());
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByText("First notes")).toBeInTheDocument();
  });

  it("replaces through the meeting-wide replace, then shows the set's new text", async () => {
    api.listNotesOutputs.mockResolvedValueOnce([extra]).mockResolvedValue([{ ...extra, notes: "Extra jobs" }]);
    const primary = props();
    render(<NotesOutputsView {...primary} />);
    fireEvent.click(await screen.findByRole("tab", { name: "Task list" }));
    fireEvent.click(screen.getByRole("button", { name: "Replace all" }));
    expect(await screen.findByText("Extra jobs")).toBeInTheDocument();
    expect(primary.onFindAndReplace).toHaveBeenCalledWith("tasks", "jobs", undefined);
  });
});
