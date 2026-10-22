import { fireEvent, render, screen } from "@testing-library/react";
import type { ComponentProps } from "react";
import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import TranscriptView from "@/components/TranscriptView";
import type { TranscriptSegment } from "@/types";

// Each line draws its badges once per render of the line, so counting badge
// renders counts line redraws.
const redrawn: string[] = [];
vi.mock("@/components/transcript/TranscriptLineBadges", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./TranscriptLineBadges")>();
  const Badges = actual.default;
  return {
    default: (props: ComponentProps<typeof Badges>) => {
      redrawn.push(props.segment.id ?? "");
      return <Badges {...props} />;
    },
  };
});

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
beforeEach(() => {
  redrawn.length = 0;
});

const TEXTS = ["alpha and beta", "gamma", "beta alpha alpha", "delta", "alpha"];
// Line i plays from 3i to 3i + 2 s, each in a group of its own.
const SEGMENTS: TranscriptSegment[] = Array.from({ length: 20 }, (_, i) => ({
  id: `u${i}`,
  speaker: `SPEAKER_${i % 2}`,
  start: i * 3,
  end: i * 3 + 2,
  text: TEXTS[i % TEXTS.length],
}));
const SPEAKER_MAP = { SPEAKER_0: "Alex", SPEAKER_1: "Blair" };
const SPEAKER_COLORS = { SPEAKER_0: "orange", SPEAKER_1: "blue" };
const NONE: never[] = [];

/** As the recording page does: new handler functions on every render. */
function Page({ time, onPlay }: { time: number; onPlay: (tag: number) => void }) {
  return (
    <TranscriptView
      recordingId="rec-1"
      segments={SEGMENTS}
      currentTime={time}
      onPlaySegment={() => onPlay(time)}
      isPlaying={false}
      onPause={() => {}}
      onResume={() => {}}
      speakerMap={SPEAKER_MAP}
      speakers={NONE}
      globalSpeakers={NONE}
      onRenameSpeaker={() => {}}
      onUpdateSegmentSpeaker={() => {}}
      onUpdateSegmentText={() => {}}
      onFindAndReplace={() => {}}
      speakerColors={SPEAKER_COLORS}
      onUndo={() => {}}
      onRedo={() => {}}
      canUndo={false}
      canRedo={false}
      onExport={() => {}}
    />
  );
}

const lineIdsWith = (pattern: RegExp) =>
  SEGMENTS.filter((segment) => pattern.test(segment.text)).map((segment) => segment.id);

describe("transcript redraws", () => {
  it("redraws only the lines a playback tick leaves and enters", () => {
    const onPlay = vi.fn();
    const { rerender } = render(<Page time={1} onPlay={onPlay} />);

    redrawn.length = 0;
    rerender(<Page time={7} onPlay={onPlay} />);
    expect(redrawn.sort()).toEqual(["u0", "u2"]);

    redrawn.length = 0;
    rerender(<Page time={7.5} onPlay={onPlay} />);
    expect(redrawn).toEqual([]);
  });

  it("plays through the page's latest handler after a tick", () => {
    const onPlay = vi.fn();
    const { rerender } = render(<Page time={1} onPlay={onPlay} />);
    rerender(<Page time={7} onPlay={onPlay} />);

    fireEvent.click(screen.getAllByTitle("Play segment")[0]);

    expect(onPlay).toHaveBeenCalledWith(7);
  });

  it("redraws only the lines whose matches change as the search moves", () => {
    render(<Page time={-1} onPlay={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Search" }));
    const find = screen.getByPlaceholderText("Find...");

    redrawn.length = 0;
    fireEvent.change(find, { target: { value: "alpha" } });
    expect(redrawn.sort()).toEqual(lineIdsWith(/alpha/).sort());

    // The first Enter makes the first match current: only its line changes.
    redrawn.length = 0;
    fireEvent.keyDown(find, { key: "Enter" });
    expect(redrawn).toEqual(["u0"]);

    // u0 has one match, so the next is u2's first: u0 and u2 change.
    redrawn.length = 0;
    fireEvent.keyDown(find, { key: "Enter" });
    expect(redrawn.sort()).toEqual(["u0", "u2"]);
  });
});
