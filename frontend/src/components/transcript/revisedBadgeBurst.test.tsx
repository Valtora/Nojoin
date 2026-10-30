import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import TranscriptView from "@/components/TranscriptView";
import type { TranscriptSegment } from "@/types";
import { viewProps } from "./transcriptTestFixtures";

vi.mock("@/lib/notificationStore", () => ({
  useNotificationStore: () => ({ addNotification: vi.fn() }),
}));

const NOW = Date.parse("2026-10-30T09:00:00Z");

// Lines stamped a millisecond apart, as one bulk write of a long transcript
// stamps them (a backfill, a reprocess, a find and replace).
const burst = (count: number, firstStamp: number): TranscriptSegment[] =>
  Array.from({ length: count }, (_, index) => ({
    id: `line-${index}`,
    revision: 1,
    speaker: index % 2 ? "B" : "A",
    start: index * 2,
    end: index * 2 + 1.5,
    text: `Line ${index} of the meeting.`,
    updated_at: new Date(firstStamp + index).toISOString(),
  }));

describe("Revised badges after a bulk write", () => {
  afterEach(() => {
    cleanup();
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("renders without an update loop when badges expire while the view renders", () => {
    // Every read of the clock lands a millisecond later, so a badge expires
    // between any two reads, as it does during a slow render of a long
    // transcript whose badges are expiring (issue #831).
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    let clock = NOW;
    vi.spyOn(Date, "now").mockImplementation(() => clock++);
    const errors = vi.spyOn(console, "error").mockImplementation(() => {});
    const lines = 600;

    render(<TranscriptView {...viewProps(burst(lines, NOW - 15_000 + 1))} />);

    const logged = errors.mock.calls.map((call) => call.map(String).join(" ")).join("\n");
    expect(logged).not.toMatch(/Maximum update depth|getSnapshot should be cached/);
    expect(screen.getByText(`Line ${lines - 1} of the meeting.`)).toBeVisible();
    expect(screen.getAllByText("Revised").length).toBeGreaterThan(0);
  });

  it("clears the rest of a burst a second after its first badge, not one line at a time", () => {
    vi.useFakeTimers({ toFake: ["Date", "setTimeout", "clearTimeout"] });
    vi.setSystemTime(NOW);
    // Stamped over 300 ms, the first line's badge 1 s from expiry.
    render(<TranscriptView {...viewProps(burst(300, NOW - 14_000))} />);
    expect(screen.getAllByText("Revised")).toHaveLength(300);
    expect(vi.getTimerCount()).toBe(1);

    act(() => vi.advanceTimersToNextTimer());
    expect(Date.now()).toBe(NOW + 1_000);
    expect(screen.getAllByText("Revised")).toHaveLength(299);

    act(() => vi.advanceTimersToNextTimer());
    expect(Date.now()).toBe(NOW + 2_000);
    expect(screen.queryByText("Revised")).not.toBeInTheDocument();
    expect(vi.getTimerCount()).toBe(0);
  });
});
