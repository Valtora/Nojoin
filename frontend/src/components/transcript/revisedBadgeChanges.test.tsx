import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import TranscriptView from "@/components/TranscriptView";
import type { TranscriptSegment } from "@/types";
import { NO_LINE_CHANGES, trackLineChanges } from "./transcriptDisplay";
import { viewProps } from "./transcriptTestFixtures";

vi.mock("@/lib/notificationStore", () => ({
  useNotificationStore: () => ({ addNotification: vi.fn() }),
}));

const NOW = Date.parse("2026-11-01T10:00:00Z");
const stamp = (msAgo: number) => new Date(NOW - msAgo).toISOString();

// Every line written a second ago, as a sample meeting, a capture's first
// transcript or an import's lands just before it is opened (issue #841).
const LINES: TranscriptSegment[] = [
  { id: "u1", speaker: "A", start: 0, end: 2, text: "Welcome, everyone.", revision: 1, updated_at: stamp(1_000) },
  { id: "u2", speaker: "B", start: 3, end: 5, text: "Thanks, Alice.", revision: 1, updated_at: stamp(1_000) },
  { id: "u3", speaker: "A", start: 6, end: 8, text: "Shall we begin?", revision: 1, updated_at: stamp(1_000) },
];

const withLine = (lines: TranscriptSegment[], id: string, change: Partial<TranscriptSegment>) =>
  lines.map((line) => (line.id === id ? { ...line, ...change } : line));

const lineElement = (id: string) => document.querySelector<HTMLElement>(`[data-segment-id="${id}"]`)!;
const revisedLines = () =>
  LINES.map(({ id }) => id!).filter((id) => within(lineElement(id)).queryByText("Revised") !== null);

describe("the Revised badge", () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ["Date", "setTimeout", "clearTimeout"] });
    vi.setSystemTime(NOW);
  });

  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  it("leaves lines written just before the view first shows them unmarked", () => {
    render(<TranscriptView {...viewProps(LINES)} />);
    expect(screen.getByText("Shall we begin?")).toBeVisible();
    expect(screen.queryByText("Revised")).not.toBeInTheDocument();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("marks a line whose text a later write changes, and only that line", () => {
    const view = render(<TranscriptView {...viewProps(LINES)} />);
    view.rerender(<TranscriptView {...viewProps(withLine(LINES, "u2", { text: "Thanks, Alicia.", updated_at: stamp(0) }))} />);
    expect(revisedLines()).toEqual(["u2"]);
  });

  it("marks a line a later write gives to another speaker", () => {
    const view = render(<TranscriptView {...viewProps(LINES)} />);
    view.rerender(<TranscriptView {...viewProps(withLine(LINES, "u3", { speaker: "B", updated_at: stamp(0) }))} />);
    expect(revisedLines()).toEqual(["u3"]);
  });

  it("leaves a line unmarked when a later write keeps its text and speaker", () => {
    const view = render(<TranscriptView {...viewProps(LINES)} />);
    const restamped = LINES.map((line) => ({ ...line, revision: 2, updated_at: stamp(0) }));
    view.rerender(<TranscriptView {...viewProps(restamped)} />);
    expect(screen.queryByText("Revised")).not.toBeInTheDocument();
  });

  it("marks a local edit once the server stamps its save", () => {
    const view = render(<TranscriptView {...viewProps(LINES)} />);
    const edited = withLine(LINES, "u1", { text: "Welcome, all." });
    view.rerender(<TranscriptView {...viewProps(edited)} />);
    expect(screen.queryByText("Revised")).not.toBeInTheDocument();
    view.rerender(<TranscriptView {...viewProps(withLine(edited, "u1", { updated_at: stamp(0) }))} />);
    expect(revisedLines()).toEqual(["u1"]);
  });

  it("treats a line added after the view opened as new, not revised", () => {
    const view = render(<TranscriptView {...viewProps(LINES)} />);
    const added: TranscriptSegment = { id: "u4", speaker: "B", start: 9, end: 11, text: "Yes.", revision: 1, updated_at: stamp(0) };
    view.rerender(<TranscriptView {...viewProps([...LINES, added])} />);
    expect(screen.getByText("Yes.")).toBeVisible();
    expect(screen.queryByText("Revised")).not.toBeInTheDocument();
  });
});

describe("trackLineChanges", () => {
  const items = LINES.map((segment) => ({ segment, segmentId: segment.id! }));

  it("keeps the same record while no line has a new stamp", () => {
    const first = trackLineChanges(NO_LINE_CHANGES, items);
    const polled = items.map(({ segment, segmentId }) => ({ segment: { ...segment }, segmentId }));
    expect(trackLineChanges(first, polled)).toBe(first);
  });

  it("remembers a line that leaves the view, so its return is not new", () => {
    const first = trackLineChanges(NO_LINE_CHANGES, items);
    const without = trackLineChanges(first, items.slice(1));
    const back = trackLineChanges(without, [
      { segmentId: "u1", segment: { ...LINES[0], text: "Welcome back.", updated_at: stamp(0) } },
    ]);
    expect(back.get("u1")?.changedAt).toBe(stamp(0));
  });
});
