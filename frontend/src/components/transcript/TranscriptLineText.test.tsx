import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";

import LiveTranscriptPanel from "@/components/LiveTranscriptPanel";
import TranscriptView from "@/components/TranscriptView";
import type { TranscriptSegment } from "@/types";

import type { TextDecoration } from "./textDecorations";
import TranscriptLineText from "./TranscriptLineText";

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

const search = (from: number, to: number, current = false): TextDecoration => ({
  kind: "search",
  from,
  to,
  current,
});

const renderLineText = (text: string, decorations: TextDecoration[]) =>
  render(
    <TranscriptLineText
      text={text}
      segmentId="utt-1"
      startMs={0}
      endMs={1000}
      decorations={decorations}
      className="line"
    />,
  ).container.querySelector("p")!;

/** Each child of the line: plain text as itself, a mark as `[text]`, current as `[*text]`. */
const pieces = (line: HTMLElement) =>
  Array.from(line.childNodes).map((node) => {
    if (!(node instanceof HTMLElement)) {
      return node.textContent;
    }
    const current = node.className.split(" ").includes("bg-action");
    return `[${current ? "*" : ""}${node.textContent}]`;
  });

const renderFinished = (segments: TranscriptSegment[]) =>
  render(
    <TranscriptView
      recordingId="rec-1"
      segments={segments}
      currentTime={-1}
      onPlaySegment={vi.fn()}
      isPlaying={false}
      onPause={vi.fn()}
      onResume={vi.fn()}
      speakerMap={{}}
      speakers={[]}
      globalSpeakers={[]}
      onRenameSpeaker={vi.fn()}
      onUpdateSegmentSpeaker={vi.fn()}
      onUpdateSegmentText={vi.fn()}
      onFindAndReplace={vi.fn()}
      speakerColors={{}}
      onUndo={vi.fn()}
      onRedo={vi.fn()}
      canUndo={false}
      canRedo={false}
      onExport={vi.fn()}
    />,
  );

describe("TranscriptLineText", () => {
  it("marks each decoration at its offsets and styles the current one", () => {
    const line = renderLineText("alpha beta alpha", [search(0, 5), search(11, 16, true)]);

    expect(pieces(line)).toEqual(["[alpha]", " beta ", "[*alpha]"]);
  });

  it("draws overlapping decorations as one mark, without repeating any text", () => {
    const line = renderLineText("banana", [search(1, 4), search(3, 6)]);

    expect(line.textContent).toBe("banana");
    expect(pieces(line)).toEqual(["b", "[anana]"]);
  });

  it("styles the whole span of the current match where others overlap it", () => {
    const line = renderLineText("aaaa", [search(0, 2, true), search(1, 3), search(2, 4)]);

    expect(pieces(line)).toEqual(["[*aa]", "[aa]"]);
  });

  it("keeps matches that only touch as separate marks", () => {
    const line = renderLineText("aa", [search(0, 1), search(1, 2)]);

    expect(pieces(line)).toEqual(["[a]", "[a]"]);
  });

  it("drops inverted and off-text decorations, and keeps a point at the end", () => {
    const off = renderLineText("abcd", [search(3, 1), search(-2, -1), search(9, 12), search(-2, 0)]);
    expect(pieces(off)).toEqual(["abcd"]);
    cleanup();

    const end = renderLineText("abcd", [search(4, 4), search(2, 9)]);
    expect(pieces(end)).toEqual(["ab", "[cd]", "[]"]);
  });

  it("renders a zero-width decoration as an empty mark at its offset", () => {
    const line = renderLineText("ab", [search(1, 1)]);

    expect(pieces(line)).toEqual(["a", "[]", "b"]);
  });

  it("names each line and its span on the timeline alike in the live and finished views", () => {
    const segments: TranscriptSegment[] = [
      { id: "utt-1", start: 1.25, end: 3.5, text: "First line.", speaker: "UNKNOWN" },
      { start: 4, end: 6.75, text: "A line saved before ids.", speaker: "UNKNOWN" },
    ];
    const attributes = (container: HTMLElement) =>
      Array.from(container.querySelectorAll<HTMLElement>("p[data-segment-id]")).map((line) => [
        line.dataset.segmentId,
        line.dataset.startMs,
        line.dataset.endMs,
      ]);

    const live = render(<LiveTranscriptPanel segments={segments} hasLoaded />);
    const liveAttributes = attributes(live.container);
    live.unmount();
    const finished = renderFinished(segments);

    expect(liveAttributes).toEqual([
      ["utt-1", "1250", "3500"],
      ["legacy-1-4-6.75-UNKNOWN", "4000", "6750"],
    ]);
    expect(attributes(finished.container)).toEqual(liveAttributes);
  });

  it("moves the current-match style forward one match per Enter in the finished view", () => {
    renderFinished([
      { id: "utt-1", start: 0, end: 2, text: "alpha beta alpha gamma alpha", speaker: "UNKNOWN" },
    ]);

    fireEvent.click(screen.getByRole("button", { name: "Search" }));
    const find = screen.getByPlaceholderText("Find...");
    fireEvent.change(find, { target: { value: "alpha" } });
    const line = document.querySelector<HTMLElement>('p[data-segment-id="utt-1"]')!;
    const currentMarks = () =>
      pieces(line)
        .filter((piece) => piece?.startsWith("["))
        .flatMap((piece, index) => (piece?.startsWith("[*") ? [index] : []));

    fireEvent.keyDown(find, { key: "Enter" });
    const [first] = currentMarks();
    fireEvent.keyDown(find, { key: "Enter" });

    expect(currentMarks()).toEqual([(first + 1) % 3]);
  });
});
