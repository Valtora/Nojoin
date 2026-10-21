import { cleanup, render } from "@testing-library/react";
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";

import LiveTranscriptPanel from "@/components/LiveTranscriptPanel";
import TranscriptView from "@/components/TranscriptView";
import type { InlineNote } from "@/lib/notes/noteOutbox";
import { expectedRenderOrder, PLACEMENT_CASES, type PlacementCase } from "@/test/placementCases";
import type { TimelineNote, TranscriptSegment } from "@/types";

vi.mock("@/lib/notificationStore", () => ({
  useNotificationStore: () => ({ addNotification: vi.fn() }),
}));

const originalScrollIntoView = Element.prototype.scrollIntoView;
beforeAll(() => {
  Element.prototype.scrollIntoView = vi.fn();
});
afterAll(() => {
  Element.prototype.scrollIntoView = originalScrollIntoView;
});

// The live and finished lists carry seconds only, as both views receive them.
const toSegments = (golden: PlacementCase, speaker: string): TranscriptSegment[] =>
  golden.segments.map((segment, index) => ({
    id: `seg-${index}`,
    start: segment.start_ms != null ? segment.start_ms / 1000 : (segment.start ?? 0),
    end: segment.end_ms != null ? segment.end_ms / 1000 : (segment.end ?? 0),
    text: segment.text,
    speaker,
    provisional: true,
    segment_source: "live",
  }));

const toNotes = (golden: PlacementCase): TimelineNote[] =>
  golden.notes.map((note) => ({
    ...note,
    recording_id: "rec-1",
    body: `Body of ${note.id}`,
    revision: 1,
    updated_at: note.created_at,
  }));

const toInline = (notes: TimelineNote[]): InlineNote[] =>
  notes
    .filter((note) => note.kind === "point" || note.kind === "selection")
    .map((note) => ({
      ...note,
      kind: note.kind as "point" | "selection",
      key: note.id,
      status: "saved",
      error: null,
    }));

/** `line:i` and `note:id` in document order. */
const renderedOrder = (container: HTMLElement, lineAttribute: string) =>
  Array.from(container.querySelectorAll<HTMLElement>(`[${lineAttribute}], [data-testid="timeline-note"]`)).map(
    (element) =>
      element.dataset.testid === "timeline-note"
        ? `note:${element.dataset.noteId}`
        : `line:${element.getAttribute(lineAttribute)}`,
  );

const liveOrder = (golden: PlacementCase) => {
  const { container } = render(
    <LiveTranscriptPanel
      segments={toSegments(golden, "UNKNOWN")}
      hasLoaded
      notes={toInline(toNotes(golden))}
    />,
  );
  const order = renderedOrder(container, "data-line-index");
  cleanup();
  return order;
};

const finishedOrder = (golden: PlacementCase) => {
  const { container } = render(
    <TranscriptView
      recordingId="rec-1"
      segments={toSegments(golden, "SPEAKER_00").map((segment) => ({
        ...segment,
        provisional: false,
        segment_source: "final",
      }))}
      timelineNotes={toNotes(golden)}
      currentTime={-1}
      onPlaySegment={vi.fn()}
      isPlaying={false}
      onPause={vi.fn()}
      onResume={vi.fn()}
      speakerMap={{ SPEAKER_00: "Dana" }}
      speakers={[]}
      globalSpeakers={[]}
      onRenameSpeaker={vi.fn()}
      onUpdateSegmentSpeaker={vi.fn()}
      onUpdateSegmentText={vi.fn()}
      onFindAndReplace={vi.fn()}
      speakerColors={{ SPEAKER_00: "green" }}
      onUndo={vi.fn()}
      onRedo={vi.fn()}
      canUndo={false}
      canRedo={false}
      onExport={vi.fn()}
    />,
  );
  const order = renderedOrder(container, "data-order-index");
  cleanup();
  return order;
};

describe("live and finished views place notes alike", () => {
  it.each(
    PLACEMENT_CASES.filter((golden) => golden.segments.length > 0).map(
      (golden) => [golden.name, golden] as const,
    ),
  )("%s", (_name, golden) => {
    const expected = expectedRenderOrder(golden);

    expect(liveOrder(golden)).toEqual(expected);
    expect(finishedOrder(golden)).toEqual(expected);
  });

  it("shows notes in a live view with no lines yet", () => {
    const golden = PLACEMENT_CASES.find((candidate) => candidate.segments.length === 0)!;

    expect(liveOrder(golden)).toEqual(expectedRenderOrder(golden));
  });

  it("splits a run of overlapping lines at a note between them", () => {
    const golden: PlacementCase = {
      name: "overlap split",
      segments: [
        { start_ms: 0, end_ms: 6000, text: "Long turn." },
        { start_ms: 5000, end_ms: 9000, text: "Overlapping reply." },
      ],
      notes: [{ id: "n1", kind: "point", anchor_ms: 4000, created_at: "2026-01-01T10:00:00Z" }],
      expected: [{ id: "n1", slot: "body", after_index: 0 }],
    };

    expect(finishedOrder(golden)).toEqual(["line:0", "note:n1", "line:1"]);
  });

  it("shows a note after a hidden line after the visible line before it", () => {
    const golden: PlacementCase = {
      name: "hidden",
      segments: [
        { start_ms: 0, end_ms: 4000, text: "Dana opens." },
        { start_ms: 5000, end_ms: 6000, text: "Unattributed aside." },
        { start_ms: 9000, end_ms: 12000, text: "Dana closes." },
      ],
      notes: [{ id: "n1", kind: "point", anchor_ms: 5500, created_at: "2026-01-01T10:00:00Z" }],
      expected: [],
    };
    const segments = toSegments(golden, "SPEAKER_00").map((segment, index) => ({
      ...segment,
      provisional: false,
      // The finished view hides UNKNOWN lines once any line has a speaker.
      speaker: index === 1 ? "UNKNOWN" : "SPEAKER_00",
    }));
    const { container } = render(
      <TranscriptView
        recordingId="rec-1"
        segments={segments}
        timelineNotes={toNotes(golden)}
        currentTime={-1}
        onPlaySegment={vi.fn()}
        isPlaying={false}
        onPause={vi.fn()}
        onResume={vi.fn()}
        speakerMap={{ SPEAKER_00: "Dana" }}
        speakers={[]}
        globalSpeakers={[]}
        onRenameSpeaker={vi.fn()}
        onUpdateSegmentSpeaker={vi.fn()}
        onUpdateSegmentText={vi.fn()}
        onFindAndReplace={vi.fn()}
        speakerColors={{ SPEAKER_00: "green" }}
        onUndo={vi.fn()}
        onRedo={vi.fn()}
        canUndo={false}
        canRedo={false}
        onExport={vi.fn()}
      />,
    );

    expect(renderedOrder(container, "data-order-index")).toEqual(["line:0", "note:n1", "line:2"]);
  });
});
