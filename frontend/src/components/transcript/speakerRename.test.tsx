import { act, fireEvent, render } from "@testing-library/react";
import type { ComponentProps } from "react";
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";

import TranscriptView from "@/components/TranscriptView";
import type { TranscriptSegment } from "@/types";

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

// Alex speaks three lines, apart from one another.
const SEGMENTS: TranscriptSegment[] = [
  { id: "u0", speaker: "SPEAKER_A", start: 0, end: 2, text: "Line zero." },
  { id: "u1", speaker: "SPEAKER_B", start: 10, end: 12, text: "Line one." },
  { id: "u2", speaker: "SPEAKER_A", start: 20, end: 22, text: "Line two." },
  { id: "u3", speaker: "SPEAKER_A", start: 30, end: 32, text: "Line three." },
];

function renderView(onRenameSpeaker: ComponentProps<typeof TranscriptView>["onRenameSpeaker"]) {
  const props = (segments: TranscriptSegment[]): ComponentProps<typeof TranscriptView> => ({
    recordingId: "rec-1",
    segments,
    currentTime: -1,
    onPlaySegment: vi.fn(),
    isPlaying: false,
    onPause: vi.fn(),
    onResume: vi.fn(),
    speakerMap: { SPEAKER_A: "Alex", SPEAKER_B: "Blair" },
    speakers: [],
    globalSpeakers: [],
    onRenameSpeaker,
    onUpdateSegmentSpeaker: vi.fn(),
    onUpdateSegmentText: vi.fn(),
    onFindAndReplace: vi.fn(),
    speakerColors: {},
    onUndo: vi.fn(),
    onRedo: vi.fn(),
    canUndo: false,
    canRedo: false,
    onExport: vi.fn(),
  });
  const view = render(<TranscriptView {...props(SEGMENTS)} />);
  return (segments: TranscriptSegment[]) => view.rerender(<TranscriptView {...props(segments)} />);
}

const renameInputs = () => Array.from(document.querySelectorAll<HTMLInputElement>('input[type="text"]'));
const lineOf = (element: Element) => element.closest("[data-order-index]")?.getAttribute("data-segment-id");

/** Double-clicks Alex's name on the line with `id`. */
async function openRename(id: string) {
  const row = document.querySelector(`[data-order-index][data-segment-id="${id}"]`);
  fireEvent.doubleClick(row!.querySelector("button")!);
  await act(async () => {});
}

describe("speaker rename", () => {
  it("opens one input, on the line double-clicked, and sends nothing as it opens", async () => {
    const onRenameSpeaker = vi.fn(async () => {});
    renderView(onRenameSpeaker);

    await openRename("u2");

    const inputs = renameInputs();
    expect(inputs.map(lineOf)).toEqual(["u2"]);
    expect(inputs[0]).toHaveValue("Alex");
    expect(document.activeElement).toBe(inputs[0]);
    expect(onRenameSpeaker).not.toHaveBeenCalled();
  });

  it("sends one request for a new name, though the input blurs while it saves", async () => {
    let finish = () => {};
    const onRenameSpeaker = vi.fn(() => new Promise<void>((resolve) => (finish = resolve)));
    renderView(onRenameSpeaker);
    await openRename("u0");
    const [input] = renameInputs();

    fireEvent.change(input, { target: { value: "  Avery " } });
    fireEvent.keyDown(input, { key: "Enter" });
    fireEvent.blur(input);
    await act(async () => finish());

    expect(onRenameSpeaker.mock.calls).toEqual([["SPEAKER_A", "Avery"]]);
    expect(renameInputs()).toEqual([]);
  });

  it("sends nothing when the input blurs with the name unchanged", async () => {
    const onRenameSpeaker = vi.fn(async () => {});
    renderView(onRenameSpeaker);
    await openRename("u0");

    fireEvent.change(renameInputs()[0], { target: { value: " Alex " } });
    fireEvent.blur(renameInputs()[0]);
    await act(async () => {});

    expect(onRenameSpeaker).not.toHaveBeenCalled();
    expect(renameInputs()).toEqual([]);
  });

  it("keeps the rename open, and sends nothing, when its line is redrawn anew", async () => {
    const onRenameSpeaker = vi.fn(async () => {});
    const rerender = renderView(onRenameSpeaker);
    await openRename("u0");
    const [before] = renameInputs();
    fireEvent.change(before, { target: { value: "Avery" } });

    // Chrome blurs a focused element as it removes it; jsdom doesn't.
    let blurredOnRemoval = 0;
    const removeChild = Node.prototype.removeChild;
    const spy = vi.spyOn(Node.prototype, "removeChild").mockImplementation(function <T extends Node>(
      this: Node,
      child: T,
    ): T {
      const focused = document.activeElement;
      if (focused instanceof HTMLElement && child.contains(focused)) {
        blurredOnRemoval += 1;
        focused.blur();
      }
      return removeChild.call(this, child) as T;
    });
    try {
      // Blair speaks over Alex's line, so it moves into a group of two lanes.
      rerender([
        SEGMENTS[0],
        { id: "u4", speaker: "SPEAKER_B", start: 1, end: 3, text: "Over the top." },
        ...SEGMENTS.slice(1),
      ]);
      await act(async () => {});
    } finally {
      spy.mockRestore();
    }

    expect(blurredOnRemoval).toBe(1);
    const inputs = renameInputs();
    expect(inputs.map(lineOf)).toEqual(["u0"]);
    expect(inputs[0]).not.toBe(before);
    expect(inputs[0]).toHaveValue("Avery");
    expect(document.activeElement).toBe(inputs[0]);
    expect(onRenameSpeaker).not.toHaveBeenCalled();
  });
});
