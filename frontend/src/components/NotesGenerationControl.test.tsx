import { act, fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import NotesGenerationControl from "./NotesGenerationControl";

vi.mock("@/lib/api", () => ({
  listNotesTemplates: vi.fn().mockResolvedValue([]),
}));

describe("Cancel in the notes generation control", () => {
  it("keeps keyboard focus while it waits, then hands it to the generate button", async () => {
    let finish!: () => void;
    const onCancel = vi.fn(() => new Promise<void>((resolve) => { finish = resolve; }));
    const props = { label: "Regenerate Notes", step: "Drafting notes, part 2 of 5", onGenerate: vi.fn(), onCancel };
    const view = render(<NotesGenerationControl {...props} isGenerating notesRunId="run-a" />);

    const cancel = screen.getByRole("button", { name: "Cancel" });
    cancel.focus();
    fireEvent.click(cancel);

    // Busy rather than disabled, which in Chrome would drop focus on the page body.
    const waiting = screen.getByRole("button", { name: "Cancelling..." });
    expect(waiting).toHaveAttribute("aria-disabled", "true");
    expect(waiting).not.toBeDisabled();
    expect(waiting).toHaveFocus();
    fireEvent.click(waiting);
    expect(onCancel).toHaveBeenCalledTimes(1);

    await act(async () => finish());
    view.rerender(<NotesGenerationControl {...props} isGenerating={false} notesRunId={null} />);

    expect(screen.queryByRole("button", { name: /^Cancel/ })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Regenerate Notes" })).toHaveFocus();
  });

  it("leaves focus alone when it moved elsewhere while the run ended", async () => {
    const onCancel = vi.fn().mockResolvedValue(undefined);
    const props = { label: "Regenerate Notes", step: null, onGenerate: vi.fn(), onCancel };
    const view = render(<><NotesGenerationControl {...props} isGenerating notesRunId="run-a" />
      <input aria-label="Title" /></>);

    const cancel = screen.getByRole("button", { name: "Cancel" });
    cancel.focus();
    await act(async () => { fireEvent.click(cancel); });
    screen.getByRole("textbox", { name: "Title" }).focus();
    view.rerender(<><NotesGenerationControl {...props} isGenerating={false} notesRunId={null} />
      <input aria-label="Title" /></>);

    expect(screen.getByRole("textbox", { name: "Title" })).toHaveFocus();
  });
});
