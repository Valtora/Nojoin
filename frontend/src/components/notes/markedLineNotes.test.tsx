import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import LiveTranscriptPanel from "@/test/SharedLiveTranscript";
import type { TranscriptSegment } from "@/types";

const LINE: TranscriptSegment = {
  id: "u1", revision: 3, start: 12, end: 15.5, text: "Budget still growing",
  speaker: "UNKNOWN", settling_required: true, provisional: true, locked: true,
};

describe("a note on a line of a recording using word settling", () => {
  it("quotes the line once it has locked", () => {
    const onNoteOnLine = vi.fn();
    const onNoteAtTime = vi.fn();
    render(<LiveTranscriptPanel hasLoaded segments={[LINE]} onNoteOnLine={onNoteOnLine} onNoteAtTime={onNoteAtTime} />);
    fireEvent.click(screen.getByRole("button", { name: "Actions for the line at 00:12" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Note on this line" }));
    expect(onNoteOnLine).toHaveBeenCalledWith({
      startMs: 12_000, endMs: 15_500, quotedText: "Budget still growing",
    });
    expect(onNoteAtTime).not.toHaveBeenCalled();
  });
});
