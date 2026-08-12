import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import LiveTranscriptPanel from "./LiveTranscriptPanel";
import { CaptureSourceChannel, TranscriptSegment } from "@/types";

const buildSegment = (
  overrides: Partial<TranscriptSegment> & { id: string },
): TranscriptSegment => ({
  start: 0,
  end: 1,
  text: "placeholder",
  speaker: "UNKNOWN",
  provisional: true,
  segment_source: "live",
  ...overrides,
});

const buildLine = (
  id: string,
  start: number,
  text: string,
  sourceChannel?: CaptureSourceChannel | null,
) =>
  buildSegment({
    id,
    start,
    end: start + 1,
    text,
    source_channel: sourceChannel,
  });

const renderPanel = (segments: TranscriptSegment[], hasLoaded = true) =>
  render(<LiveTranscriptPanel segments={segments} hasLoaded={hasLoaded} />);

/**
 * jsdom performs no layout, so scroll geometry is always zero. Fake it so the
 * pin logic can be exercised at all.
 */
const setScrollGeometry = (
  element: HTMLElement,
  geometry: { scrollTop: number; scrollHeight: number; clientHeight: number },
) => {
  Object.defineProperty(element, "scrollHeight", {
    configurable: true,
    value: geometry.scrollHeight,
  });
  Object.defineProperty(element, "clientHeight", {
    configurable: true,
    value: geometry.clientHeight,
  });
  element.scrollTop = geometry.scrollTop;
};

describe("LiveTranscriptPanel", () => {
  it("states the expected latency while waiting for the first utterance", () => {
    renderPanel([]);

    expect(screen.getByText("Listening.")).toBeInTheDocument();
    expect(
      screen.getByText(/a few seconds behind the conversation/i),
    ).toBeInTheDocument();
  });

  it("distinguishes a paused capture from an active one", () => {
    render(<LiveTranscriptPanel segments={[]} hasLoaded isPaused />);

    expect(screen.getByText("Recording is paused.")).toBeInTheDocument();
  });

  it("says it is still loading before the first fetch resolves", () => {
    renderPanel([], false);

    expect(screen.getByText(/loading the transcript so far/i)).toBeInTheDocument();
  });

  it("renders each utterance with its elapsed timestamp", () => {
    renderPanel([
      buildLine("a", 12, "so the migration ran clean"),
      buildLine("b", 3671, "and the rollback path was tested"),
    ]);

    expect(screen.getByText("00:12")).toBeInTheDocument();
    expect(screen.getByText("1:01:11")).toBeInTheDocument();
    expect(screen.getAllByTestId("live-transcript-line")).toHaveLength(2);
  });

  it("labels the capture channel only where it changes", () => {
    renderPanel([
      buildLine("a", 0, "first shared line", "system"),
      buildLine("b", 5, "second shared line", "system"),
      buildLine("c", 10, "a microphone line", "microphone"),
      buildLine("d", 15, "back to shared", "system"),
    ]);

    // Two consecutive shared-audio lines share one label; the run that follows
    // gets its own, so a switch of source reads as an event rather than noise.
    expect(screen.getAllByText("Shared audio")).toHaveLength(2);
    expect(screen.getAllByText("Microphone")).toHaveLength(1);
  });

  it("shows no channel label when the source was not attributable", () => {
    // Overlapping speech and regions with no dominant channel serialize as
    // null. Absence reads as "not sure"; a guess would read as fact.
    renderPanel([
      buildLine("a", 0, "clear microphone line", "microphone"),
      buildLine("b", 5, "people talking over each other", null),
      buildLine("c", 10, "still ambiguous", undefined),
    ]);

    expect(screen.getAllByText("Microphone")).toHaveLength(1);
    expect(screen.queryByText("Shared audio")).not.toBeInTheDocument();
    expect(screen.getAllByTestId("live-transcript-line")).toHaveLength(3);
  });

  it("never presents the microphone channel as the viewer", () => {
    // A capture with no shared tab audio puts the whole room on the microphone
    // channel, so identity wording here would misattribute every other speaker.
    renderPanel([buildLine("a", 0, "someone else in the room", "microphone")]);

    expect(screen.getByText("Microphone")).toBeInTheDocument();
    expect(screen.queryByText(/^You$/)).not.toBeInTheDocument();
    expect(screen.queryByText(/^Others$/)).not.toBeInTheDocument();
  });

  it("offers a jump back to the latest line once scrolled away", () => {
    renderPanel([
      buildLine("a", 0, "first"),
      buildLine("b", 5, "second"),
    ]);

    const container = screen.getByTestId("live-transcript-scroll");
    expect(
      screen.queryByRole("button", { name: /jump to latest/i }),
    ).not.toBeInTheDocument();

    setScrollGeometry(container, {
      scrollTop: 0,
      scrollHeight: 1000,
      clientHeight: 300,
    });
    fireEvent.scroll(container);

    const jumpButton = screen.getByRole("button", { name: /jump to latest/i });

    setScrollGeometry(container, {
      scrollTop: 700,
      scrollHeight: 1000,
      clientHeight: 300,
    });
    fireEvent.click(jumpButton);

    expect(
      screen.queryByRole("button", { name: /jump to latest/i }),
    ).not.toBeInTheDocument();
  });

  it("keeps following new lines while pinned to the bottom", () => {
    const { rerender } = renderPanel([buildLine("a", 0, "first")]);
    const container = screen.getByTestId("live-transcript-scroll");

    setScrollGeometry(container, {
      scrollTop: 0,
      scrollHeight: 500,
      clientHeight: 300,
    });

    rerender(
      <LiveTranscriptPanel
        segments={[buildLine("a", 0, "first"), buildLine("b", 5, "second")]}
        hasLoaded
      />,
    );

    expect(container.scrollTop).toBe(500);
  });

  it("follows a provisional line that grows without adding a line", () => {
    const { rerender } = renderPanel([
      buildLine("a", 0, "first"),
      buildLine("b", 5, "second"),
    ]);
    const container = screen.getByTestId("live-transcript-scroll");

    setScrollGeometry(container, {
      scrollTop: 0,
      scrollHeight: 500,
      clientHeight: 300,
    });

    // An utterance is rewritten in place as it finalises, so the text wraps to
    // more rows while the count stays put. Keying the follow on the line count
    // missed this and let the tail drift below the fold.
    rerender(
      <LiveTranscriptPanel
        segments={[
          buildLine("a", 0, "first"),
          buildLine("b", 5, "second, and then a good deal more of it"),
        ]}
        hasLoaded
      />,
    );

    expect(container.scrollTop).toBe(500);
  });
});
