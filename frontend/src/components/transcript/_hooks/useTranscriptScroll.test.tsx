import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { TranscriptSegment } from "@/types";
import { useTranscriptScroll } from "./useTranscriptScroll";

const line = (id: string, text = id): TranscriptSegment => ({
  id, text, speaker: "UNKNOWN", start: 0, end: 1,
});
const initial = [line("a"), line("b")];

function Harness({ segments = initial, height = 1000, liveFollow = true, growth,
  activeSegmentKey = null }: {
  segments?: TranscriptSegment[];
  height?: number;
  liveFollow?: boolean;
  growth?: unknown;
  activeSegmentKey?: string | null;
}) {
  const { scrollContainerRef, isFollowingLatest, jumpToLatest, updateScrollAnchor } =
    useTranscriptScroll({ segments, activeSegmentKey, liveFollow, growth });
  return <div ref={(element) => {
    const region = element?.querySelector<HTMLElement>('[data-testid="region"]');
    if (!region) return;
    Object.defineProperty(region, "scrollHeight", { configurable: true, value: height });
    Object.defineProperty(region, "clientHeight", { configurable: true, value: 300 });
    region.scrollTo = vi.fn((options?: ScrollToOptions | number, y?: number) => {
      region.scrollTop = typeof options === "number" ? y ?? 0 : options?.top ?? 0;
    });
  }}>
    <output data-testid="following">{String(isFollowingLatest)}</output>
    <button onClick={jumpToLatest}>Jump</button>
    <div data-testid="region" onScroll={updateScrollAnchor} ref={scrollContainerRef}>
      {segments.map((segment, index) => <div key={segment.id} id={`segment-${segment.id}`}
        data-segment-id={segment.id} data-order-index={index} ref={(element) => {
          if (!element) return;
          Object.defineProperty(element, "offsetTop", { configurable: true, value: index * 350 });
          Object.defineProperty(element, "offsetHeight", { configurable: true, value: 40 });
        }}>{segment.text}</div>)}
    </div>
  </div>;
}

function region(height = 1000): HTMLElement {
  const element = screen.getByTestId("region");
  expect(element.scrollHeight).toBe(height);
  expect(element.clientHeight).toBe(300);
  const second = element.querySelector<HTMLElement>('[data-order-index="1"]')!;
  expect(second).toHaveAttribute("data-segment-id", "b");
  expect(second.offsetTop).toBe(350);
  expect(second.offsetHeight).toBe(40);
  return element;
}

describe("shared transcript live follow", () => {
  it("positions the initial transcript at the tail and follows a new line", () => {
    const view = render(<Harness />);
    const element = region();
    expect(element.scrollTop).toBe(1000);
    expect(screen.getByTestId("following")).toHaveTextContent("true");
    view.rerender(<Harness segments={[...initial, line("c")]} height={1300} />);
    expect(region(1300).scrollTop).toBe(1300);
  });

  it("follows same-ID text growth while preserving its row node", () => {
    const view = render(<Harness />);
    const element = region();
    const row = element.querySelector('[data-segment-id="b"]');
    view.rerender(<Harness segments={[initial[0], line("b", "a much longer tail")]} height={1200} />);
    expect(element.querySelector('[data-segment-id="b"]')).toBe(row);
    expect(region(1200).scrollTop).toBe(1200);
  });

  it.each(["append", "growth"])("preserves the reader's position after scrolling away during %s", (change) => {
    const view = render(<Harness />);
    const element = region();
    element.scrollTop = 100;
    fireEvent.scroll(element);
    expect(screen.getByTestId("following")).toHaveTextContent("false");
    view.rerender(<Harness segments={change === "append" ? [...initial, line("c")]
      : [initial[0], line("b", "the tail grows")]} height={1200} />);
    expect(region(1200).scrollTop).toBe(100);
    expect(screen.getByTestId("following")).toHaveTextContent("false");
  });

  it("jumps from the reader's position to the tail and resumes following", () => {
    const view = render(<Harness />);
    const element = region();
    element.scrollTop = 100;
    fireEvent.scroll(element);
    expect(element.scrollTop).toBe(100);
    fireEvent.click(screen.getByRole("button", { name: "Jump" }));
    expect(element.scrollTop).toBe(1000);
    expect(screen.getByTestId("following")).toHaveTextContent("true");
    view.rerender(<Harness segments={[...initial, line("c")]} height={1300} />);
    expect(region(1300).scrollTop).toBe(1300);
  });

  it.each([[32, true], [33, false]])("treats %i pixels from the bottom as following=%s", (distance, follows) => {
    render(<Harness />);
    const element = region();
    element.scrollTop = 1000 - 300 - distance;
    fireEvent.scroll(element);
    expect(screen.getByTestId("following")).toHaveTextContent(String(follows));
  });

  it("follows a note-only tail growth without changing segments", () => {
    const view = render(<Harness growth={0} />);
    const element = region();
    view.rerender(<Harness growth={1} height={1200} />);
    expect(region(1200).scrollTop).toBe(1200);
    expect(element.scrollTop).toBe(1200);
  });

  it("keeps the current viewport when note-only growth is followed by settled handoff", () => {
    const view = render(<Harness growth={0} />);
    const element = region();
    view.rerender(<Harness growth={1} height={1200} />);
    expect(region(1200).scrollTop).toBe(1200);
    view.rerender(<Harness growth={1} height={1200} liveFollow={false} />);
    expect(element.scrollTop).toBe(1200);
  });

  it("retains the live tail at handoff until playback changes its active line", () => {
    const view = render(<Harness activeSegmentKey="a" />);
    const element = region();
    expect(element.scrollTop).toBe(1000);
    view.rerender(<Harness activeSegmentKey="a" liveFollow={false} />);
    expect(region().scrollTop).toBe(1000);
    expect(element.scrollTo).not.toHaveBeenCalled();
    view.rerender(<Harness activeSegmentKey="b" liveFollow={false} />);
    expect(region().scrollTop).toBe(220);
    expect(element.scrollTo).toHaveBeenCalledWith({ top: 220, behavior: "smooth" });
  });

  it("does not let playback-follow move a detached live reader", () => {
    const view = render(<Harness />);
    const element = region();
    element.scrollTop = 100;
    fireEvent.scroll(element);
    view.rerender(<Harness activeSegmentKey="a" />);
    expect(element.scrollTop).toBe(100);
    expect(element.scrollTo).not.toHaveBeenCalled();
  });

});
