import { renderWithFillContract as render } from "@/test/fillMarkerContracts";
import type { ComponentProps } from "react";
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import TranscriptView from "@/components/TranscriptView";
import type { TranscriptSegment } from "@/types";

vi.mock("@/lib/notificationStore", () => ({
  useNotificationStore: () => ({ addNotification: vi.fn() }),
}));

// A [0,5) overlaps B [3,8); C [9,12) overlaps D [10,14); E [20,22). H, an
// unknown speaker's line, isn't shown, as the transcript has known speakers.
const SEGMENTS: TranscriptSegment[] = [
  { id: "A", speaker: "S0", start: 0, end: 5, text: "a", overlapping_speakers: ["S0", "S1"] },
  { id: "B", speaker: "S1", start: 3, end: 8, text: "b", overlapping_speakers: ["S0", "S1"] },
  { id: "H", speaker: "UNKNOWN", start: 9, end: 10, text: "h" },
  { id: "C", speaker: "S0", start: 9, end: 12, text: "c" },
  { id: "D", speaker: "S1", start: 10, end: 14, text: "d" },
  { id: "E", speaker: "S0", start: 20, end: 22, text: "e" },
];

// jsdom has no layout: each line's text sits at its own height, far below a
// view with no height, so following a line always scrolls to centre it.
const TOP: Record<string, number> = { A: 1000, B: 1100, C: 2000, D: 2100, E: 3000 };
const LINE_HEIGHT = 40;
const centreOf = (id: string) => TOP[id] + LINE_HEIGHT / 2;

const originalOffsetTop = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "offsetTop");
const originalOffsetHeight = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "offsetHeight");
const originalScrollTo = Element.prototype.scrollTo;
const scrolledTo = vi.fn();
beforeAll(() => {
  Object.defineProperty(HTMLElement.prototype, "offsetTop", {
    configurable: true,
    get() {
      return TOP[(this as HTMLElement).id.replace(/^segment-/, "")] ?? 0;
    },
  });
  Object.defineProperty(HTMLElement.prototype, "offsetHeight", {
    configurable: true,
    get: () => LINE_HEIGHT,
  });
  Element.prototype.scrollTo = function (options?: ScrollToOptions | number) {
    if (typeof options === "object") scrolledTo(options.top);
  } as Element["scrollTo"];
});
afterEach(() => scrolledTo.mockClear());
afterAll(() => {
  if (originalOffsetTop) Object.defineProperty(HTMLElement.prototype, "offsetTop", originalOffsetTop);
  if (originalOffsetHeight) {
    Object.defineProperty(HTMLElement.prototype, "offsetHeight", originalOffsetHeight);
  }
  Element.prototype.scrollTo = originalScrollTo;
});

const props = (currentTime: number): ComponentProps<typeof TranscriptView> => ({
  recordingId: "rec-1",
  segments: SEGMENTS,
  currentTime,
  onPlaySegment: () => {},
  isPlaying: true,
  onPause: () => {},
  onResume: () => {},
  speakerMap: {},
  speakers: [],
  globalSpeakers: [],
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

/** Plays through `times`, returning where follow scrolled at each. */
function play(times: number[]): number[][] {
  const view = render(<TranscriptView {...props(-1)} />);
  return times.map((time) => {
    scrolledTo.mockClear();
    view.rerender(<TranscriptView {...props(time)} />);
    return scrolledTo.mock.calls.map(([top]) => top as number);
  });
}

describe("playback-follow", () => {
  it("follows the line that continues when the one it overlapped ends", () => {
    // A starts, B joins it, A ends at 5 while B goes on.
    const [atStart, atThree, atFive] = play([0, 3, 5]);

    expect(atStart).toEqual([centreOf("A")]);
    // Follow stays on A while B joins it.
    expect(atThree).toEqual([]);
    expect(atFive).toEqual([centreOf("B")]);
  });

  it("follows the continuing line of a later overlap too", () => {
    // C starts with the hidden H, D joins it, C ends at 12 while D goes on.
    const [atNine, , atTwelve] = play([9, 10, 12]);

    expect(atNine).toEqual([centreOf("C")]);
    expect(atTwelve).toEqual([centreOf("D")]);
  });
});
