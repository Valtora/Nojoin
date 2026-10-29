import { act, fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import LiveLineEditor from "./LiveLineEditor";
import type { TranscriptSegment } from "@/types";

describe("pending live line correction", () => {
  it("holds the submitted text read-only while saving, then closes only after acceptance", async () => {
    const segment: TranscriptSegment = {
      id: "u1", revision: 3, start: 0, end: 1, speaker: "LIVE_PROVISIONAL_1", text: "Original",
    };
    let finish!: (accepted: boolean) => void;
    const editText = vi.fn(() => new Promise<boolean>((resolve) => { finish = resolve; }));
    const close = vi.fn();
    render(<LiveLineEditor kind="editText" segment={segment} anchor={document.createElement("button")}
      handlers={{ editText, changeSpeaker: vi.fn(), split: vi.fn() }} onClose={close} />);
    const editor = screen.getByRole("textbox", { name: "Line text" });
    fireEvent.change(editor, { target: { value: "My submitted correction" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(editor).toBeDisabled();
    expect(editor).toHaveValue("My submitted correction");
    expect(close).not.toHaveBeenCalled();
    await act(async () => finish(false));
    expect(editor).toBeEnabled();
    expect(editor).toHaveValue("My submitted correction");
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(editor).toBeDisabled();
    await act(async () => finish(true));
    expect(editText).toHaveBeenCalledTimes(2);
    expect(close).toHaveBeenCalledTimes(1);
  });
});
