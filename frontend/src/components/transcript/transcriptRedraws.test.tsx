import { fireEvent, render, screen } from "@testing-library/react";
import type { ComponentProps } from "react";
import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import TranscriptView from "@/components/TranscriptView";
import { applyTranscriptDelta, type LocalTranscriptState } from "@/lib/transcriptState";
import type { RecordingSpeaker, TranscriptSegment, TranscriptUtterance } from "@/types";

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

// Each group formats its start time when it draws, and nothing else here
// formats a time, so the times formatted name the groups that redrew.
const formatted = new Set<number>();
const groupsRedrawn = () => [...formatted].sort((left, right) => left - right);
vi.mock("@/components/transcript/transcriptDisplay", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./transcriptDisplay")>();
  return {
    ...actual,
    formatTime: (seconds: number) => {
      formatted.add(seconds);
      return actual.formatTime(seconds);
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
const forget = () => {
  redrawn.length = 0;
  formatted.clear();
};
beforeEach(forget);

const TEXTS = ["alpha and beta", "gamma", "beta alpha alpha", "delta", "alpha"];
// Line i plays from 3i to 3i + 2 s, each in a group of its own.
const SEGMENTS: TranscriptSegment[] = Array.from({ length: 20 }, (_, i) => ({
  id: `u${i}`,
  speaker: `SPEAKER_${i % 2}`,
  start: i * 3,
  end: i * 3 + 2,
  text: TEXTS[i % TEXTS.length],
}));
const speaker = (label: string, name: string): RecordingSpeaker => ({
  id: label === "SPEAKER_0" ? 1 : 2,
  recording_id: "rec-1",
  diarization_label: label,
  local_name: name,
  created_at: "2026-10-22T00:00:00Z",
  updated_at: "2026-10-22T00:00:00Z",
});

interface PageProps {
  time: number;
  onPlay?: (tag: number) => void;
  isPlaying?: boolean;
  segments?: TranscriptSegment[];
}

/**
 * As the recording page does: new handler functions on every render, and the
 * speakers, their names and colours as new objects with the same contents.
 */
function Page({ time, onPlay = () => {}, isPlaying = false, segments = SEGMENTS }: PageProps) {
  return (
    <TranscriptView
      recordingId="rec-1"
      segments={segments}
      currentTime={time}
      onPlaySegment={() => onPlay(time)}
      isPlaying={isPlaying}
      onPause={() => {}}
      onResume={() => {}}
      speakerMap={{ SPEAKER_0: "Alex", SPEAKER_1: "Blair" }}
      speakers={[speaker("SPEAKER_0", "Alex"), speaker("SPEAKER_1", "Blair")]}
      globalSpeakers={[]}
      onRenameSpeaker={() => {}}
      onUpdateSegmentSpeaker={() => {}}
      onUpdateSegmentText={() => {}}
      onFindAndReplace={() => {}}
      speakerColors={{ SPEAKER_0: "orange", SPEAKER_1: "blue" }}
      onUndo={() => {}}
      onRedo={() => {}}
      canUndo={false}
      canRedo={false}
      onExport={() => {}}
      pendingRemoteUtteranceIds={[]}
    />
  );
}

const lineIdsWith = (pattern: RegExp) =>
  SEGMENTS.filter((segment) => pattern.test(segment.text)).map((segment) => segment.id);

describe("transcript redraws", () => {
  it("redraws only the lines a playback tick leaves and enters", () => {
    const { rerender } = render(<Page time={1} />);

    forget();
    rerender(<Page time={7} />);
    expect(redrawn.sort()).toEqual(["u0", "u2"]);

    forget();
    rerender(<Page time={7.5} />);
    expect(redrawn).toEqual([]);
    expect(groupsRedrawn()).toEqual([]);
  });

  it("plays through the page's latest handler after a tick", () => {
    const onPlay = vi.fn();
    const { rerender } = render(<Page time={1} onPlay={onPlay} />);
    rerender(<Page time={7} onPlay={onPlay} />);

    fireEvent.click(screen.getAllByTitle("Play segment")[0]);

    expect(onPlay).toHaveBeenCalledWith(7);
  });

  it("redraws only the playing group, and no line, when playback starts or stops", () => {
    const { rerender } = render(<Page time={7} />);

    forget();
    rerender(<Page time={7} isPlaying />);
    expect(groupsRedrawn()).toEqual([6]);
    expect(redrawn).toEqual([]);

    forget();
    rerender(<Page time={7} />);
    expect(groupsRedrawn()).toEqual([6]);
  });

  it("redraws nothing when the poll hands over equal speakers, names and colours", () => {
    const { rerender } = render(<Page time={7} />);

    forget();
    rerender(<Page time={7} />);

    expect(redrawn).toEqual([]);
    expect(groupsRedrawn()).toEqual([]);
  });

  it("redraws only the line a delta changes", () => {
    const { rerender } = render(<Page time={7} />);
    const changed = SEGMENTS.map((segment, i) =>
      i === 4 ? { ...segment, text: `${segment.text} again` } : segment,
    );

    forget();
    rerender(<Page time={7} segments={changed} />);

    expect(redrawn).toEqual(["u4"]);
    expect(groupsRedrawn()).toEqual([12]);
  });

  describe("a full sync", () => {
    // The lines as the API hands them over, new objects on every sync.
    const utterances = (): TranscriptUtterance[] =>
      SEGMENTS.map(({ id, start, end, text, speaker }) => ({ id: id!, start, end, text, speaker, revision: 1 }));
    const fullSync = (currentState: LocalTranscriptState | null, next: TranscriptUtterance[]) =>
      applyTranscriptDelta({
        currentState,
        recordingId: "rec-1",
        fallbackSegments: [],
        delta: { recording_id: "rec-1", revision: 1, utterances: next, tombstones: [], speakers: [] },
        mode: "full",
      });

    it("redraws nothing when it hands over the same lines", () => {
      const first = fullSync(null, utterances());
      const { rerender } = render(<Page time={7} segments={first.segments} />);

      forget();
      rerender(<Page time={7} segments={fullSync(first, utterances()).segments} />);

      expect(redrawn).toEqual([]);
      expect(groupsRedrawn()).toEqual([]);
    });

    it("redraws only the line it changes, with its new text", () => {
      const first = fullSync(null, utterances());
      const { rerender } = render(<Page time={7} segments={first.segments} />);
      const next = utterances();
      next[4] = { ...next[4], text: "alpha, revised", revision: 2 };

      forget();
      rerender(<Page time={7} segments={fullSync(first, next).segments} />);

      expect(redrawn).toEqual(["u4"]);
      expect(groupsRedrawn()).toEqual([12]);
      expect(screen.getByText("alpha, revised")).toBeInTheDocument();
    });
  });

  it("redraws only the lines and groups whose matches change as the search moves", () => {
    render(<Page time={-1} />);
    fireEvent.click(screen.getByRole("button", { name: "Search" }));
    const find = screen.getByPlaceholderText("Find...");

    forget();
    fireEvent.change(find, { target: { value: "alpha" } });
    expect(redrawn.sort()).toEqual(lineIdsWith(/alpha/).sort());

    // The first Enter makes the first match current: only its line changes.
    forget();
    fireEvent.keyDown(find, { key: "Enter" });
    expect(redrawn).toEqual(["u0"]);
    expect(groupsRedrawn()).toEqual([0]);

    // u0 has one match, so the next is u2's first: u0 and u2 change.
    forget();
    fireEvent.keyDown(find, { key: "Enter" });
    expect(redrawn.sort()).toEqual(["u0", "u2"]);
    expect(groupsRedrawn()).toEqual([0, 6]);
  });

  it("redraws only the line a delta changes while the search is open", () => {
    const { rerender } = render(<Page time={-1} />);
    fireEvent.click(screen.getByRole("button", { name: "Search" }));
    fireEvent.change(screen.getByPlaceholderText("Find..."), { target: { value: "alpha" } });
    const changed = SEGMENTS.map((segment, i) =>
      i === 4 ? { ...segment, text: "alpha, then alpha" } : segment,
    );

    forget();
    rerender(<Page time={-1} segments={changed} />);

    // u4 draws twice: for its text, then for its matches, found after.
    expect(new Set(redrawn)).toEqual(new Set(["u4"]));
    expect(groupsRedrawn()).toEqual([12]);
  });

  it("redraws only the changed line of a group while the search is open", () => {
    // u2 and u3 overlap, so they share a group, and both match.
    const overlapping = SEGMENTS.map((segment, i) =>
      i === 3 ? { ...segment, start: 7, end: 10, text: "alpha delta" } : segment,
    );
    const { rerender } = render(<Page time={-1} segments={overlapping} />);
    fireEvent.click(screen.getByRole("button", { name: "Search" }));
    fireEvent.change(screen.getByPlaceholderText("Find..."), { target: { value: "alpha" } });
    const changed = overlapping.map((segment, i) =>
      i === 2 ? { ...segment, text: "beta alpha" } : segment,
    );

    forget();
    rerender(<Page time={-1} segments={changed} />);

    expect(new Set(redrawn)).toEqual(new Set(["u2"]));
  });
});
