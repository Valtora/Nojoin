import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import LiveTranscriptPanel from "@/components/LiveTranscriptPanel";
import { forgetNoteOutboxes } from "@/lib/notes/noteOutboxes";
import type { InlineNote } from "@/lib/notes/noteOutbox";
import { useNoteEdits } from "@/lib/notes/useNoteEdits";
import type { TimelineNote, TranscriptSegment } from "@/types";

import CapturesGallery from "./CapturesGallery";
import MyNotesList from "./MyNotesList";

const segments: TranscriptSegment[] = [
  { id: "u0", start: 0, end: 2, text: "Opening", speaker: "UNKNOWN" },
  { id: "u1", start: 10, end: 12, text: "The budget", speaker: "UNKNOWN" },
];
const note: TimelineNote & { kind: "point" } = {
  id: "n1", recording_id: "rec-1", kind: "point", body: "Check the budget", anchor_ms: 11_000,
  revision: 1, created_at: "2026-10-27T10:00:00Z", updated_at: "2026-10-27T10:00:00Z",
  attachment: {
    id: 7, title: "budget.png", file_type: "image/png", status: "PENDING",
    use_in_notes: false, readable: true, is_image: true,
  },
};

function Page({ surface, folded, onShowTranscript }: {
  surface: "notes" | "captures";
  folded: boolean;
  onShowTranscript: () => void;
}) {
  const noteEdits = useNoteEdits("rec-1");
  const notes = folded ? [note, { ...note, id: "n2", body: "Ask finance", attachment: null }] : [note];
  const inline: InlineNote[] = notes.map((item) => ({ ...item, key: item.id, status: "saved", error: null }));
  const props = { recordingId: "rec-1", timelineNotes: notes, segments, noteEdits, onShowTranscript };
  return (
    <>
      {surface === "notes" ? <MyNotesList {...props} onPlay={vi.fn()} disablePlayback /> : <CapturesGallery {...props} />}
      <div id="panel-transcript">
        <LiveTranscriptPanel segments={segments} hasLoaded notes={inline} />
      </div>
    </>
  );
}

const place = (element: Element, top: number, height: number) =>
  vi.spyOn(element, "getBoundingClientRect").mockReturnValue({ top, height } as DOMRect);

describe("side-panel jumps into the live transcript", () => {
  beforeEach(() => forgetNoteOutboxes());
  afterEach(() => vi.restoreAllMocks());

  it.each([
    ["notes", false], ["captures", false], ["notes", true], ["captures", true],
  ] as const)("%s centres the %s-folded note's target without moving the page", (surface, folded) => {
    const onShowTranscript = vi.fn();
    const pageScroll = vi.spyOn(window, "scrollTo").mockImplementation(() => {});
    render(<Page surface={surface} folded={folded} onShowTranscript={onShowTranscript} />);
    const region = screen.getByTestId("live-transcript-scroll");
    region.scrollTo = vi.fn();
    Object.defineProperty(region, "clientHeight", { configurable: true, value: 400 });
    region.scrollTop = 50;
    place(region, 100, 400);
    const line = region.querySelector('[data-order-index="1"]')!;
    place(line, 300, 60);
    const body = within(region).queryByTestId("timeline-note");
    if (folded) {
      expect(body).toBeNull();
    } else {
      expect(body).toHaveAttribute("data-note-id", note.id);
      place(body!, 900, 40);
    }
    const item = surface === "notes"
      ? screen.getAllByTestId("my-note")[0]
      : screen.getByTestId("capture-item");
    fireEvent.click(within(item).getByRole("button", {
      name: surface === "notes" ? /^Show in the transcript/ : /^Show the note at/,
    }));

    expect(onShowTranscript).toHaveBeenCalledTimes(1);
    // Distinct geometry proves a shown note beats the preceding transcript row.
    expect(region.scrollTo).toHaveBeenCalledExactlyOnceWith({
      top: folded ? 50 + 200 - (400 - 60) / 2 : 50 + 800 - (400 - 40) / 2,
      behavior: "smooth",
    });
    expect(pageScroll).not.toHaveBeenCalled();
  });
});
