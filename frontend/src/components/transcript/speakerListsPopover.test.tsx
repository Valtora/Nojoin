import { cleanup, fireEvent, render } from "@testing-library/react";
import type { ComponentProps } from "react";
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import TranscriptView from "@/components/TranscriptView";
import type { GlobalSpeaker, RecordingSpeaker, TranscriptSegment } from "@/types";

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
afterEach(cleanup);

const recordingSpeaker = (label: string, name: string, id: number): RecordingSpeaker => ({
  id,
  recording_id: "rec-1",
  diarization_label: label,
  local_name: name,
  created_at: "2026-10-22T00:00:00Z",
  updated_at: "2026-10-22T00:00:00Z",
});
const person = (id: number, name: string): GlobalSpeaker => ({
  id,
  name,
  created_at: "2026-10-22T00:00:00Z",
  updated_at: "2026-10-22T00:00:00Z",
});

const SEGMENTS: TranscriptSegment[] = [
  { id: "u0", speaker: "SPEAKER_0", start: 0, end: 2, text: "alpha" },
  { id: "u1", speaker: "SPEAKER_1", start: 3, end: 5, text: "beta" },
];

const props = (
  speakers: readonly RecordingSpeaker[],
  globalSpeakers: GlobalSpeaker[],
): ComponentProps<typeof TranscriptView> => ({
  recordingId: "rec-1",
  segments: SEGMENTS,
  currentTime: -1,
  onPlaySegment: () => {},
  isPlaying: false,
  onPause: () => {},
  onResume: () => {},
  speakerMap: { SPEAKER_0: "Alex", SPEAKER_1: "Blair" },
  speakers,
  globalSpeakers,
  onRenameSpeaker: () => {},
  onUpdateSegmentSpeaker: () => {},
  onUpdateSegmentText: () => {},
  onFindAndReplace: () => {},
  speakerColors: {},
  onUndo: () => {},
  onRedo: () => {},
  canUndo: false,
  canRedo: false,
  onExport: () => {},
});

const openPopoverOn = (container: HTMLElement, id: string) => {
  fireEvent.click(container.querySelector<HTMLButtonElement>(`[data-segment-id="${id}"] button`)!);
};

// People are listed only for what is typed; "a" is in every name below.
const searchPopover = (value: string) => {
  fireEvent.change(document.body.querySelector<HTMLInputElement>('input[placeholder="Search or add..."]')!,
    { target: { value } });
};

// The speaker lists pass through the view's content-stable copies and the
// memoised lines to the popover, which renders in a portal.
describe("speaker lists in the speaker popover", () => {
  it("shows a renamed recording speaker and a new library person while open", () => {
    const view = render(
      <TranscriptView
        {...props([recordingSpeaker("SPEAKER_0", "Alex", 1), recordingSpeaker("SPEAKER_1", "Blair", 2)], [person(10, "Gina")])}
      />,
    );
    openPopoverOn(view.container, "u0");
    searchPopover("a");
    expect(document.body.textContent).toContain("Blair");
    expect(document.body.textContent).toContain("Gina");

    view.rerender(
      <TranscriptView
        {...props(
          [recordingSpeaker("SPEAKER_0", "Alex", 1), recordingSpeaker("SPEAKER_1", "Bea", 2)],
          [person(10, "Gina"), person(11, "Hal")],
        )}
      />,
    );
    expect(document.body.textContent).toContain("Bea");
    expect(document.body.textContent).toContain("Hal");
  });

  it("shows a change that follows an equal-content poll", () => {
    const speakers = (second: string) => [
      recordingSpeaker("SPEAKER_0", "Alex", 1),
      recordingSpeaker("SPEAKER_1", second, 2),
    ];
    const view = render(<TranscriptView {...props(speakers("Blair"), [])} />);
    view.rerender(<TranscriptView {...props(speakers("Blair"), [])} />);
    view.rerender(<TranscriptView {...props(speakers("Bea"), [])} />);

    openPopoverOn(view.container, "u0");

    expect(document.body.textContent).toContain("Bea");
  });

  it("opens and searches with a frozen empty speaker list", () => {
    const frozen: readonly RecordingSpeaker[] = Object.freeze([]);
    const view = render(<TranscriptView {...props(frozen, [person(10, "Gina")])} />);

    expect(() => {
      openPopoverOn(view.container, "u0");
      const input = document.body.querySelector<HTMLInputElement>("input");
      if (input) fireEvent.change(input, { target: { value: "gi" } });
    }).not.toThrow();
    expect(document.body.textContent).toContain("Gina");
  });

  it("shows a new library person when only the library changes", () => {
    const speakers = [recordingSpeaker("SPEAKER_0", "Alex", 1), recordingSpeaker("SPEAKER_1", "Blair", 2)];
    const view = render(<TranscriptView {...props(speakers, [person(10, "Gina")])} />);
    openPopoverOn(view.container, "u0");
    searchPopover("a");

    view.rerender(<TranscriptView {...props(speakers, [person(10, "Gina"), person(11, "Hal")])} />);

    expect(document.body.textContent).toContain("Hal");
  });
});
