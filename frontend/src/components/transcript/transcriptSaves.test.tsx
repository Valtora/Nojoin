import { act, fireEvent, render, screen } from "@testing-library/react";
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

// Alex and Blair take turns, apart from one another.
const SEGMENTS: TranscriptSegment[] = [
  { id: "u0", speaker: "SPEAKER_A", start: 0, end: 2, text: "Line zero.", revision: 1 },
  { id: "u1", speaker: "SPEAKER_B", start: 10, end: 12, text: "Line one.", revision: 1 },
  { id: "u2", speaker: "SPEAKER_A", start: 20, end: 22, text: "Line two.", revision: 1 },
];

type Props = ComponentProps<typeof TranscriptView>;

/** A save that runs until the test ends it. */
function heldSave() {
  const finishes: Array<() => void> = [];
  const save = vi.fn<(...args: unknown[]) => Promise<void>>(
    () => new Promise<void>((resolve) => finishes.push(resolve)),
  );
  /** Ends the save sent `index`th, counting from 0. */
  const finish = (index: number) => act(async () => finishes[index]());
  return { save, finish };
}

/** Renders the view, and returns a rerender that changes some of its props. */
function renderView(overrides: Partial<Props>) {
  const props = (changes: Partial<Props> = {}): Props => ({
    recordingId: "rec-1",
    segments: SEGMENTS,
    currentTime: -1,
    onPlaySegment: vi.fn(),
    isPlaying: false,
    onPause: vi.fn(),
    onResume: vi.fn(),
    speakerMap: { SPEAKER_A: "Alex", SPEAKER_B: "Blair" },
    speakers: [],
    globalSpeakers: [],
    onRenameSpeaker: vi.fn(async () => {}),
    onUpdateSegmentSpeaker: vi.fn(),
    onUpdateSegmentText: vi.fn(async () => {}),
    onFindAndReplace: vi.fn(async () => {}),
    speakerColors: {},
    onUndo: vi.fn(),
    onRedo: vi.fn(),
    canUndo: false,
    canRedo: false,
    onExport: vi.fn(),
    ...overrides,
    ...changes,
  });
  const view = render(<TranscriptView {...props()} />);
  return async (changes: Partial<Props>) => {
    view.rerender(<TranscriptView {...props(changes)} />);
    await act(async () => {});
  };
}

const row = (id: string) => document.querySelector(`[data-order-index][data-segment-id="${id}"]`)!;
const lineOf = (element: Element | null) =>
  element?.closest("[data-order-index]")?.getAttribute("data-segment-id");
const textEditor = () => document.querySelector("textarea");
const renameInput = () => document.querySelector<HTMLInputElement>('input[type="text"]');

async function openText(id: string) {
  fireEvent.click(row(id).querySelector('[aria-haspopup="menu"]')!);
  fireEvent.click(screen.getByRole("menuitem", { name: "Edit text" }));
  await act(async () => {});
}

async function openRename(id: string) {
  fireEvent.doubleClick(row(id).querySelector("button")!);
  await act(async () => {});
}

/** Types into the open editor and presses Enter. */
async function submit(editor: HTMLElement | null, value: string) {
  fireEvent.change(editor!, { target: { value } });
  fireEvent.keyDown(editor!, { key: "Enter" });
  await act(async () => {});
}

/** Moves focus off the open editor, as a click elsewhere on the page does. */
async function leave(editor: HTMLElement | null) {
  fireEvent.blur(editor!);
  await act(async () => {});
}

describe("transcript saves", () => {
  it("sends a line's text while a rename saves", async () => {
    const rename = heldSave();
    const onUpdateSegmentText = vi.fn(async () => {});
    renderView({ onRenameSpeaker: rename.save, onUpdateSegmentText });

    await openRename("u0");
    await submit(renameInput(), "Avery");
    await leave(renameInput());
    await openText("u1");
    await submit(textEditor(), "Line one, edited.");

    expect(onUpdateSegmentText).toHaveBeenCalledWith(SEGMENTS[1], "Line one, edited.");
    await rename.finish(0);
  });

  it("sends a line's text while another line's text saves", async () => {
    const text = heldSave();
    renderView({ onUpdateSegmentText: text.save });

    await openText("u0");
    await submit(textEditor(), "Line zero, edited.");
    await leave(textEditor());
    await openText("u1");
    await submit(textEditor(), "Line one, edited.");

    expect(text.save.mock.calls).toEqual([
      [SEGMENTS[0], "Line zero, edited."],
      [SEGMENTS[1], "Line one, edited."],
    ]);
    await text.finish(0);
    await text.finish(1);
  });

  it("leaves a line's text opened during another save open when that save ends", async () => {
    const text = heldSave();
    renderView({ onUpdateSegmentText: text.save });
    await openText("u0");
    await submit(textEditor(), "Line zero, edited.");
    await leave(textEditor());

    await openText("u1");
    fireEvent.change(textEditor()!, { target: { value: "Line one, half" } });
    await text.finish(0);

    expect(lineOf(textEditor())).toBe("u1");
    expect(textEditor()).toHaveValue("Line one, half");
  });

  it("leaves a rename opened during another rename's save open when that save ends", async () => {
    const rename = heldSave();
    renderView({ onRenameSpeaker: rename.save });
    await openRename("u0");
    await submit(renameInput(), "Avery");
    await leave(renameInput());

    await openRename("u1");
    fireEvent.change(renameInput()!, { target: { value: "Bry" } });
    await rename.finish(0);

    expect(lineOf(renameInput())).toBe("u1");
    expect(renameInput()).toHaveValue("Bry");
  });

  it("leaves a rename reopened during its save open when the save ends, though it shows the new name", async () => {
    const rename = heldSave();
    const rerender = renderView({ onRenameSpeaker: rename.save });
    await openRename("u0");
    await submit(renameInput(), "Avery");
    await leave(renameInput());

    // The page reads the recording again before the rename's save ends.
    await rerender({ speakerMap: { SPEAKER_A: "Avery", SPEAKER_B: "Blair" } });
    await openRename("u2");
    expect(renameInput()).toHaveValue("Avery");
    await rename.finish(0);

    expect(lineOf(renameInput())).toBe("u2");
  });

  it("leaves a line's text opened during a rename's save open when that save ends", async () => {
    const rename = heldSave();
    renderView({ onRenameSpeaker: rename.save });
    await openRename("u0");
    await submit(renameInput(), "Avery");
    await leave(renameInput());

    await openText("u2");
    await rename.finish(0);

    expect(lineOf(textEditor())).toBe("u2");
  });

  it("holds Replace and Replace All back until the last of two saves ends", async () => {
    const text = heldSave();
    const rename = heldSave();
    renderView({ onUpdateSegmentText: text.save, onRenameSpeaker: rename.save });
    fireEvent.click(screen.getByRole("button", { name: "Find & Replace" }));
    fireEvent.change(screen.getByPlaceholderText("Find..."), { target: { value: "Line" } });
    const replace = screen.getByRole("button", { name: /^Replace$/ });
    const replaceAll = screen.getByRole("button", { name: /Replace All/ });

    await openText("u0");
    await submit(textEditor(), "Line zero, edited.");
    await leave(textEditor());
    await openRename("u1");
    await submit(renameInput(), "Bryn");
    expect(replace).toBeDisabled();

    await text.finish(0);
    expect(replace).toBeDisabled();
    expect(replaceAll).toBeDisabled();

    await rename.finish(0);
    expect(replace).toBeEnabled();
    expect(replaceAll).toBeEnabled();
  });

  it("sends a line's text once though the editor blurs while it saves, and closes it then", async () => {
    const text = heldSave();
    renderView({ onUpdateSegmentText: text.save });
    await openText("u0");

    await submit(textEditor(), "Line zero, edited.");
    await leave(textEditor());
    await text.finish(0);

    expect(text.save).toHaveBeenCalledTimes(1);
    expect(textEditor()).toBeNull();
  });

  it("closes the editor when its save ends, with nothing typed since", async () => {
    const text = heldSave();
    renderView({ onUpdateSegmentText: text.save });
    await openText("u0");

    await submit(textEditor(), "Line zero, edited.");
    expect(lineOf(textEditor())).toBe("u0");
    await text.finish(0);

    expect(textEditor()).toBeNull();
  });

  it("keeps the editor open for text typed after Enter, and sends it on the next Enter", async () => {
    const text = heldSave();
    renderView({ onUpdateSegmentText: text.save });
    await openText("u0");
    await submit(textEditor(), "Line zero");
    fireEvent.change(textEditor()!, { target: { value: "Line zero, and more" } });
    await text.finish(0);

    expect(textEditor()).toHaveValue("Line zero, and more");
    await submit(textEditor(), "Line zero, and more");

    expect(text.save.mock.calls.map(([, sent]) => sent)).toEqual([
      "Line zero",
      "Line zero, and more",
    ]);
  });

  it("sends nothing from a line reopened during its save and left as it opened", async () => {
    const text = heldSave();
    const rerender = renderView({ onUpdateSegmentText: text.save });
    await openText("u0");
    await submit(textEditor(), "Line zero, edited.");
    await leave(textEditor());

    // Reopened before the save lands, the line still holds its old text.
    await openText("u0");
    expect(textEditor()).toHaveValue("Line zero.");
    await text.finish(0);
    await rerender({
      segments: [{ ...SEGMENTS[0], text: "Line zero, edited.", revision: 2 }, ...SEGMENTS.slice(1)],
    });
    await leave(textEditor());

    expect(text.save).toHaveBeenCalledTimes(1);
    expect(textEditor()).toBeNull();
  });

  it("keeps a refused text and sends it again on the next Enter", async () => {
    const onUpdateSegmentText = vi.fn().mockResolvedValueOnce(false).mockResolvedValueOnce(true);
    renderView({ onUpdateSegmentText });
    await openText("u0");
    await submit(textEditor(), "Typed while read-only");

    expect(textEditor()).toHaveValue("Typed while read-only");
    await submit(textEditor(), "Typed while read-only");

    expect(onUpdateSegmentText).toHaveBeenCalledTimes(2);
    expect(textEditor()).toBeNull();
  });

  it("lets Export go once a new result replaces the line being edited", async () => {
    const rerender = renderView({});
    await openText("u0");
    expect(screen.getByRole("button", { name: "Export transcript disabled" })).toBeDisabled();

    await rerender({ segments: [{ ...SEGMENTS[0], id: "n0" }, ...SEGMENTS.slice(1)] });

    expect(textEditor()).toBeNull();
    expect(screen.getByRole("button", { name: "Export transcript" })).toBeEnabled();
  });
});
