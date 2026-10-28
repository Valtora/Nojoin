import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import LiveTranscriptPanel from "@/test/SharedLiveTranscript";
import type { TranscriptSegment } from "@/types";

const LINE: TranscriptSegment = {
  id: "u1", revision: 3, start: 12, end: 15.5, text: "Budget still growing",
  speaker: "UNKNOWN", settling_required: true, provisional: true, locked: false,
};

describe("a note on a line of a recording using word settling", () => {
  it("waits for the line to lock, then quotes it", () => {
    const onNoteOnLine = vi.fn();
    const view = render(<LiveTranscriptPanel hasLoaded segments={[LINE]} onNoteOnLine={onNoteOnLine} />);
    fireEvent.click(screen.getByRole("button", { name: "Actions for the line at 00:12" }));
    const open = screen.getByRole("menuitem", { name: /Note on this line/ });
    expect(open).toHaveAttribute("aria-disabled", "true");
    expect(open).toHaveTextContent("This line is still settling.");
    fireEvent.click(open);
    expect(onNoteOnLine).not.toHaveBeenCalled();

    // The menu stays open on a disabled item, and the item enables once the line locks.
    view.rerender(<LiveTranscriptPanel hasLoaded segments={[{ ...LINE, locked: true }]} onNoteOnLine={onNoteOnLine} />);
    fireEvent.click(screen.getByRole("menuitem", { name: "Note on this line" }));
    expect(onNoteOnLine).toHaveBeenCalledWith({
      startMs: 12_000, endMs: 15_500, quotedText: "Budget still growing",
    });
  });
});
