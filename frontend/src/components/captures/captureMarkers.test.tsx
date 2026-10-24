import { fireEvent, render, screen } from "@testing-library/react";
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";

import LiveTranscriptPanel from "@/components/LiveTranscriptPanel";
import TranscriptView from "@/components/TranscriptView";
import type { Capture } from "@/lib/api";
import type { TranscriptSegment } from "@/types";

import { captureItems } from "./captureItems";

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

const LINES: TranscriptSegment[] = [
  { id: "a", start: 2, end: 6, text: "Let us begin.", speaker: "SPEAKER_00" },
  // Overlaps the first: without a capture between them they form one group.
  { id: "b", start: 5, end: 9, text: "Here is the plan.", speaker: "SPEAKER_01" },
  { id: "c", start: 30, end: 33, text: "Forty thousand.", speaker: "SPEAKER_00" },
];

const capture = (id: number, captureMs: number, extra: Partial<Capture> = {}): Capture =>
  ({
    id,
    recording_id: "rec-1",
    kind: "capture",
    capture_ms: captureMs,
    use_in_notes: false,
    title: "Capture",
    file_type: "image/png",
    status: "READY",
    created_at: "2026-10-24T09:00:00",
    ...extra,
  }) as Capture;

/** `line:i` and `capture:key` in document order. */
const order = (container: HTMLElement, lineAttribute: string) =>
  Array.from(
    container.querySelectorAll<HTMLElement>(`[${lineAttribute}], [data-testid="capture-marker"]`),
  ).map((element) =>
    element.dataset.testid === "capture-marker"
      ? `capture:${element.dataset.captureKey}`
      : `line:${element.getAttribute(lineAttribute)}`,
  );

const ITEMS = captureItems(
  [capture(1, 1_000), capture(2, 3_000, { use_in_notes: true }), capture(3, 40_000)],
  [],
);

describe("a capture's marker in the transcript", () => {
  it("sits after the line in progress when it was taken, in the finished view", () => {
    const onOpen = vi.fn();
    const { container } = render(
      <TranscriptView
        recordingId="rec-1"
        segments={LINES}
        currentTime={-1}
        onPlaySegment={vi.fn()}
        isPlaying={false}
        onPause={vi.fn()}
        onResume={vi.fn()}
        speakerMap={{ SPEAKER_00: "Alice Example", SPEAKER_01: "Bob Example" }}
        speakers={[]}
        globalSpeakers={[]}
        onRenameSpeaker={vi.fn()}
        onUpdateSegmentSpeaker={vi.fn()}
        onUpdateSegmentText={vi.fn()}
        onFindAndReplace={vi.fn()}
        speakerColors={{}}
        onUndo={vi.fn()}
        onRedo={vi.fn()}
        canUndo={false}
        canRedo={false}
        onExport={vi.fn()}
        captures={ITEMS}
        onOpenCapture={onOpen}
      />,
    );

    expect(order(container, "data-order-index")).toEqual([
      "capture:capture-1",
      "line:0",
      // It ends the overlapping run there, as a note would.
      "capture:capture-2",
      "line:1",
      "line:2",
      "capture:capture-3",
    ]);
    expect(screen.getAllByText("Used in notes")).toHaveLength(1);

    fireEvent.click(screen.getByRole("button", { name: "Open the capture at 00:03" }));
    expect(onOpen).toHaveBeenCalledWith(ITEMS[1].capture);
  });

  it("sits in the same place in the live view, with an unsaved one marked", () => {
    const onDiscard = vi.fn();
    const items = captureItems(
      [capture(1, 1_000)],
      [
        {
          key: "p-1",
          recordingId: "rec-1",
          captureMs: 31_000,
          preview: null,
          status: "failed",
          error: "Network Error",
        },
      ],
    );
    const { container } = render(
      <LiveTranscriptPanel
        segments={LINES}
        hasLoaded
        captures={items}
        onOpenCapture={vi.fn()}
        onDiscardCapture={onDiscard}
      />,
    );

    expect(order(container, "data-line-index")).toEqual([
      "capture:capture-1",
      "line:0",
      "line:1",
      "line:2",
      "capture:p-1",
    ]);
    fireEvent.click(screen.getByRole("button", { name: "Discard the capture at 00:31" }));
    expect(onDiscard).toHaveBeenCalledWith("p-1");
  });
});
