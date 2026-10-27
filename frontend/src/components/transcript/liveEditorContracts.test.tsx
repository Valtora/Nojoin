import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { TranscriptSegment } from "@/types";
import LiveLineEditor, { type LiveLineHandlers } from "./LiveLineEditor";
import LiveSpeakerChip from "./LiveSpeakerChip";

const line: TranscriptSegment = { id: "u1", revision: 3, text: "Original", speaker: "LIVE_PROVISIONAL_1", start: 0, end: 1 };

describe("live editor command retention", () => {
  it("keeps a refused name, retries it, and retains the rename callback from opening", async () => {
    const liveRename = vi.fn().mockResolvedValueOnce(false).mockResolvedValueOnce(true);
    const finishedRename = vi.fn();
    const props = { label: line.speaker, name: "Speaker 1", locked: true, onRename: liveRename };
    const view = render(<LiveSpeakerChip {...props} />);
    fireEvent.click(screen.getByRole("button", { name: "Rename Speaker 1" }));
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "Alex" } });
    view.rerender(<LiveSpeakerChip {...props} onRename={finishedRename} />);
    fireEvent.keyDown(screen.getByRole("combobox"), { key: "Enter" });
    await waitFor(() => expect(liveRename).toHaveBeenCalledTimes(1));
    expect(screen.getByRole("combobox")).toHaveValue("Alex");
    fireEvent.keyDown(screen.getByRole("combobox"), { key: "Enter" });
    await waitFor(() => expect(screen.queryByRole("combobox")).not.toBeInTheDocument());
    expect(liveRename).toHaveBeenCalledTimes(2);
    expect(finishedRename).not.toHaveBeenCalled();
  });

  it("retains a changed draft typed while a live rename saves", async () => {
    let finish!: () => void;
    const rename = vi.fn(() => new Promise<void>((resolve) => { finish = resolve; }));
    render(<LiveSpeakerChip label={line.speaker} name="Speaker 1" locked onRename={rename} />);
    fireEvent.click(screen.getByRole("button", { name: "Rename Speaker 1" }));
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "Alex" } });
    fireEvent.keyDown(screen.getByRole("combobox"), { key: "Enter" });
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "Alexandra" } });
    await act(async () => finish());
    expect(screen.getByRole("combobox")).toHaveValue("Alexandra");
  });

  it("submits the original line and handlers after a live-to-finished source switch", async () => {
    const original: LiveLineHandlers = {
      editText: vi.fn().mockResolvedValue(false), changeSpeaker: vi.fn().mockResolvedValue(true), split: vi.fn().mockResolvedValue(true),
    };
    const finished = { ...original, editText: vi.fn().mockResolvedValue(true) };
    const anchor = document.createElement("button");
    const close = vi.fn();
    const props = { kind: "editText" as const, segment: line, anchor, handlers: original, onClose: close };
    const view = render(<LiveLineEditor {...props} />);
    fireEvent.change(screen.getByRole("textbox", { name: "Line text" }), { target: { value: "My correction" } });
    view.rerender(<LiveLineEditor {...props} segment={{ ...line, revision: 9 }} handlers={finished} />);
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(original.editText).toHaveBeenCalledWith(line, "My correction"));
    expect(finished.editText).not.toHaveBeenCalled();
    expect(close).not.toHaveBeenCalled();
    expect(screen.getByRole("textbox")).toHaveValue("My correction");
  });
});
