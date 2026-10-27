import { act, cleanup, render, screen } from "@testing-library/react";
import type { ComponentProps } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import TranscriptView from "@/components/TranscriptView";
import { withProcessTimeZone } from "@/test/processTimeZone";
import type { TranscriptSegment } from "@/types";

vi.mock("@/lib/notificationStore", () => ({
  useNotificationStore: () => ({ addNotification: vi.fn() }),
}));

const NOW = Date.parse("2026-10-27T16:08:13Z");
const PROPS: ComponentProps<typeof TranscriptView> = {
  recordingId: "rec-utc",
  segments: [],
  currentTime: -1,
  onPlaySegment: () => {},
  isPlaying: false,
  onPause: () => {},
  onResume: () => {},
  speakerMap: { SPEAKER_0: "Alex" },
  speakers: [],
  globalSpeakers: [],
  onRenameSpeaker: () => {},
  onUpdateSegmentSpeaker: () => {},
  onUpdateSegmentText: () => {},
  onFindAndReplace: () => {},
  speakerColors: { SPEAKER_0: "orange" },
  onUndo: () => {},
  onRedo: () => {},
  canUndo: false,
  canRedo: false,
  onExport: () => {},
};

const segment = (id: string, updatedAt: string, speaker = "SPEAKER_0"): TranscriptSegment => ({
  id,
  speaker,
  start: 0,
  end: 2,
  text: id,
  updated_at: updatedAt,
});

describe.each(["America/New_York", "Asia/Tokyo"])("Revised badge for server timestamps in %s", (timeZone) => {
  withProcessTimeZone(timeZone);

  beforeEach(() => {
    vi.useFakeTimers({ toFake: ["Date", "setTimeout", "clearTimeout"] });
    vi.setSystemTime(NOW);
  });

  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  it("expires the badge while playback is paused without another render", () => {
    render(
      <TranscriptView
        {...PROPS}
        segments={[{
          id: "line-utc",
          speaker: "SPEAKER_0",
          start: 0,
          end: 2,
          text: "Freshly revised line",
          updated_at: "2026-10-27T16:08:13",
        }]}
      />,
    );

    expect(screen.getByText("Revised")).toBeVisible();
    act(() => vi.advanceTimersByTime(15_001));
    expect(screen.queryByText("Revised")).not.toBeInTheDocument();
  });

  it("uses one timer for successive visible expiries and then stops", () => {
    render(
      <TranscriptView
        {...PROPS}
        segments={[
          segment("Newer line", "2026-10-27T16:08:12"),
          segment("Older line", "2026-10-27T16:08:01"),
          segment("Hidden line", "2026-10-27T16:07:59", "UNKNOWN"),
        ]}
      />,
    );

    expect(screen.queryByText("Hidden line")).not.toBeInTheDocument();
    expect(screen.getAllByText("Revised")).toHaveLength(2);
    expect(vi.getTimerCount()).toBe(1);

    act(() => vi.advanceTimersToNextTimer());
    expect(Date.now()).toBe(NOW + 3_000);
    expect(screen.getAllByText("Revised")).toHaveLength(1);
    expect(vi.getTimerCount()).toBe(1);

    act(() => vi.advanceTimersByTime(10_999));
    expect(screen.getByText("Revised")).toBeVisible();
    act(() => vi.advanceTimersByTime(1));
    expect(screen.queryByText("Revised")).not.toBeInTheDocument();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("replaces the timer on segment changes and clears it on removal and unmount", () => {
    const { rerender, unmount } = render(
      <TranscriptView {...PROPS} segments={[segment("Edited line", "2026-10-27T16:08:03")]} />,
    );
    act(() => vi.advanceTimersByTime(2_000));
    rerender(
      <TranscriptView {...PROPS} segments={[segment("Edited line", "2026-10-27T16:08:15")]} />,
    );
    expect(vi.getTimerCount()).toBe(1);

    act(() => vi.advanceTimersByTime(3_000));
    expect(screen.getByText("Revised")).toBeVisible();
    act(() => vi.advanceTimersByTime(12_000));
    expect(screen.queryByText("Revised")).not.toBeInTheDocument();
    expect(vi.getTimerCount()).toBe(0);

    act(() => vi.advanceTimersByTime(60_000));
    const freshSegments = [segment("New revision after idle", new Date().toISOString())];
    rerender(<TranscriptView {...PROPS} segments={freshSegments} />);
    expect(screen.getByText("Revised")).toBeVisible();
    expect(vi.getTimerCount()).toBe(1);

    rerender(<TranscriptView {...PROPS} segments={[]} />);
    expect(vi.getTimerCount()).toBe(0);
    rerender(<TranscriptView {...PROPS} segments={freshSegments} />);
    expect(vi.getTimerCount()).toBe(1);
    unmount();
    expect(vi.getTimerCount()).toBe(0);
  });

  it.each([
    ["zone-less UTC five seconds old", "2026-10-27T16:08:08", true],
    ["zone-less UTC one hour old", "2026-10-27T15:08:13", false],
    ["the five-second future clock-skew boundary", "2026-10-27T16:08:18", true],
    ["beyond the future clock-skew allowance", "2026-10-27T16:08:18.001", false],
    ["the exclusive fifteen-second age boundary", "2026-10-27T16:07:58", false],
    ["just inside the age boundary", "2026-10-27T16:07:58.001", true],
    ["an explicit UTC zone", "2026-10-27T16:08:08Z", true],
    ["a positive offset", "2026-10-27T18:08:08+02:00", true],
    ["a negative offset", "2026-10-27T12:08:08-04:00", true],
    ["an aware stale timestamp", "2026-10-27T15:08:13Z", false],
    ["an aware timestamp too far ahead", "2026-10-27T16:08:18.001Z", false],
    ["an invalid timestamp", "not-a-timestamp", false],
    ["a missing timestamp", undefined, false],
  ])("handles %s", (_name, updatedAt, revised) => {
    render(
      <TranscriptView
        {...PROPS}
        segments={[{
          id: "line-utc",
          speaker: "SPEAKER_0",
          start: 0,
          end: 2,
          text: "Timestamp regression line",
          updated_at: updatedAt,
        }]}
      />,
    );

    expect(screen.getByText("Timestamp regression line")).toBeVisible();
    if (revised) expect(screen.getByText("Revised")).toBeVisible();
    else expect(screen.queryByText("Revised")).not.toBeInTheDocument();
    expect(vi.getTimerCount()).toBe(revised ? 1 : 0);
  });
});
