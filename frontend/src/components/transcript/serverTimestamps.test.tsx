import { render, screen } from "@testing-library/react";
import type { ComponentProps } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import TranscriptView from "@/components/TranscriptView";

vi.mock("@/lib/notificationStore", () => ({
  useNotificationStore: () => ({ addNotification: vi.fn() }),
}));

const NOW = Date.parse("2026-10-27T16:08:13Z");
const originalTimeZone = process.env.TZ;
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

describe.each(["America/New_York", "Asia/Tokyo"])("Revised badge for server timestamps in %s", (timeZone) => {
  beforeEach(() => {
    process.env.TZ = timeZone;
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(NOW);
  });

  afterEach(() => {
    vi.useRealTimers();
    if (originalTimeZone === undefined) delete process.env.TZ;
    else process.env.TZ = originalTimeZone;
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
  });
});
