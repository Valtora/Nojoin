import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
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

// The design system's focus ring, which the Button and IconButton primitives
// carry (docs/DESIGN.md, "The flat canon").
const FOCUS_RING = "focus-visible:outline-focus-ring";
// IconButton at sm: a 2.5rem box. jsdom has no layout, so the classes stand in.
const ICON_TARGET = ["h-10", "w-10"];
// 44 px on a touch screen in every density: px, so a 15 px root can't shrink it.
const TOUCH_HEIGHT = "pointer-coarse:h-[44px]";
const TOUCH_WIDTH = "pointer-coarse:w-[44px]";

const SEGMENTS: TranscriptSegment[] = [
  { id: "u0", speaker: "SPEAKER_0", start: 0, end: 2, text: "alpha and beta" },
  { id: "u1", speaker: "SPEAKER_1", start: 65, end: 67, text: "beta alpha" },
];

function transcriptView(overrides: Partial<ComponentProps<typeof TranscriptView>> = {}) {
  return (
    <TranscriptView
      recordingId="rec-1"
      segments={SEGMENTS}
      currentTime={-1}
      onPlaySegment={vi.fn()}
      isPlaying={false}
      onPause={vi.fn()}
      onResume={vi.fn()}
      speakerMap={{ SPEAKER_0: "Alex", SPEAKER_1: "Blair" }}
      speakers={[]}
      globalSpeakers={[]}
      onRenameSpeaker={vi.fn()}
      onUpdateSegmentSpeaker={vi.fn()}
      onUpdateSegmentText={vi.fn()}
      onFindAndReplace={vi.fn()}
      speakerColors={{ SPEAKER_0: "orange", SPEAKER_1: "blue" }}
      onUndo={vi.fn()}
      onRedo={vi.fn()}
      canUndo
      canRedo
      onExport={vi.fn()}
      {...overrides}
    />
  );
}

const renderView = (overrides: Partial<ComponentProps<typeof TranscriptView>> = {}) =>
  render(transcriptView(overrides));

const button = (name: string | RegExp) => screen.getByRole("button", { name });
const findField = () => screen.getByPlaceholderText("Find...");
const settingsOpen = () => screen.queryByRole("checkbox", { name: "Case Sensitive" }) !== null;

/** Opens Find & Replace with a term that matches, and the settings menu. */
function openEverything() {
  fireEvent.click(button("Find & Replace"));
  fireEvent.change(findField(), { target: { value: "alpha" } });
  fireEvent.click(button("Advanced Search Settings"));
}

const expectIconTarget = (control: HTMLElement, name: string) => {
  const classes = control.className.split(" ");
  for (const size of [...ICON_TARGET, TOUCH_HEIGHT, TOUCH_WIDTH]) {
    expect(classes, `${name}: ${size}`).toContain(size);
  }
};

describe("transcript toolbar", () => {
  it("names every icon button, rings it, and gives it a full target", () => {
    renderView();
    openEverything();

    for (const name of [
      "Undo",
      "Redo",
      "Export transcript",
      "Search",
      "Find & Replace",
      "Previous match",
      "Next match",
      "Advanced Search Settings",
    ]) {
      const control = button(name);
      expect(control.className, name).toContain(FOCUS_RING);
      expectIconTarget(control, name);
    }
  });

  it("rings Find Next, Replace and Replace All, with a touch target", () => {
    renderView();
    openEverything();

    for (const name of [/Find Next/, /^Replace$/, /Replace All/]) {
      const control = button(name);
      expect(control.className).toContain(FOCUS_RING);
      expect(control.className).toContain(TOUCH_HEIGHT);
      // "All" is narrower than 44 px on a phone without it.
      expect(control.className).toContain("pointer-coarse:min-w-[44px]");
    }
  });

  it("rings the fields and the search options with the focus ring", () => {
    renderView();
    openEverything();

    const controls = [
      findField(),
      screen.getByPlaceholderText("Replace..."),
      ...screen.getAllByRole("checkbox"),
    ];
    expect(controls).toHaveLength(5);
    for (const control of controls) {
      expect(control.className).toContain(FOCUS_RING);
      expect(control.className).not.toContain("focus:ring");
    }
  });

  it("moves between matches with the named arrows", () => {
    renderView();
    fireEvent.click(button("Search"));
    expect(button("Previous match")).toBeDisabled();
    expect(button("Next match")).toBeDisabled();

    fireEvent.change(findField(), { target: { value: "alpha" } });
    expect(button("Next match")).toBeEnabled();
    expect(screen.getByText("1 of 2")).toBeInTheDocument();

    fireEvent.click(button("Next match"));
    expect(screen.getByText("2 of 2")).toBeInTheDocument();
    fireEvent.click(button("Next match"));
    expect(screen.getByText("1 of 2")).toBeInTheDocument();
    fireEvent.click(button("Previous match"));
    expect(screen.getByText("2 of 2")).toBeInTheDocument();
  });

  it("makes a query's first match current as it is typed", () => {
    renderView();
    fireEvent.click(button("Search"));

    fireEvent.change(findField(), { target: { value: "alpha" } });

    expect(screen.getByText("1 of 2")).toBeInTheDocument();
    // The current match is drawn in the action colour, on the first line.
    const current = document.querySelectorAll("mark.bg-action");
    expect(current).toHaveLength(1);
    expect(current[0].closest("[data-segment-id]")).toHaveAttribute("data-segment-id", "u0");
  });

  it("starts a new query at its first match, even one that repeats a cleared query", () => {
    renderView();
    fireEvent.click(button("Search"));
    fireEvent.change(findField(), { target: { value: "alpha" } });
    fireEvent.click(button("Next match"));
    expect(screen.getByText("2 of 2")).toBeInTheDocument();

    fireEvent.change(findField(), { target: { value: "beta" } });
    expect(screen.getByText("1 of 2")).toBeInTheDocument();

    fireEvent.change(findField(), { target: { value: "" } });
    fireEvent.change(findField(), { target: { value: "beta" } });
    expect(screen.getByText("1 of 2")).toBeInTheDocument();
  });

  it("starts at the first match when unticking an option gives the query matches", () => {
    renderView();
    fireEvent.click(button("Find & Replace"));
    fireEvent.click(button("Advanced Search Settings"));
    fireEvent.click(screen.getByRole("checkbox", { name: "Case Sensitive" }));
    fireEvent.change(findField(), { target: { value: "ALPHA" } });
    expect(screen.queryByText(/ of 2/)).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("checkbox", { name: "Case Sensitive" }));

    expect(screen.getByText("1 of 2")).toBeInTheDocument();
  });

  it("starts at the first match when a line that matches arrives", () => {
    const view = renderView();
    fireEvent.click(button("Search"));
    fireEvent.change(findField(), { target: { value: "budget" } });

    view.rerender(
      transcriptView({
        segments: [...SEGMENTS, { id: "u2", speaker: "SPEAKER_0", start: 90, end: 92, text: "the budget" }],
      }),
    );

    expect(screen.getByText("1 of 1")).toBeInTheDocument();
  });

  it("announces the match count from one polite live region", () => {
    renderView();
    fireEvent.click(button("Search"));
    const region = document.querySelector('[aria-live="polite"]');
    expect(region).toHaveAttribute("aria-atomic", "true");
    expect(region).toHaveTextContent(/^$/);

    // Typing the query is enough: no key press.
    fireEvent.change(findField(), { target: { value: "alpha" } });
    expect(document.querySelector('[aria-live="polite"]')).toBe(region);
    expect(region).toHaveTextContent("1 of 2");

    fireEvent.keyDown(findField(), { key: "Enter" });
    expect(region).toHaveTextContent("2 of 2");
  });

  it("moves focus from a match arrow to the find field when the matches run out", () => {
    renderView();
    fireEvent.click(button("Search"));
    fireEvent.change(findField(), { target: { value: "alpha" } });
    button("Next match").focus();

    fireEvent.change(findField(), { target: { value: "no such words" } });

    expect(button("Next match")).toBeDisabled();
    expect(document.activeElement).toBe(findField());
  });

  it("moves focus from Replace to the find field when it replaced the last match", () => {
    renderView();
    fireEvent.click(button("Find & Replace"));
    fireEvent.change(findField(), { target: { value: "alpha" } });
    button(/^Replace$/).focus();

    fireEvent.change(findField(), { target: { value: "no such words" } });

    expect(document.activeElement).toBe(findField());
  });

  it("moves focus to the find field when a saving Replace is disabled and blurred", () => {
    // Chrome blurs a focused button as it is disabled, firing focusout; jsdom
    // doesn't, so the focusout is dispatched by hand.
    renderView({ onUpdateSegmentText: vi.fn(() => new Promise<void>(() => {})) });
    fireEvent.click(button("Find & Replace"));
    fireEvent.change(findField(), { target: { value: "alpha" } });
    const replace = button(/^Replace$/);
    replace.focus();

    fireEvent.click(replace);
    expect(replace).toBeDisabled();
    fireEvent.focusOut(replace);

    expect(document.activeElement).toBe(findField());
  });

  it("sends focus to Find & Replace when Replace All finishes", async () => {
    const onFindAndReplace = vi.fn(() => Promise.resolve());
    renderView({ onFindAndReplace });
    fireEvent.click(button("Find & Replace"));
    fireEvent.change(findField(), { target: { value: "alpha" } });
    const replaceAll = button(/Replace All/);
    replaceAll.focus();

    fireEvent.click(replaceAll);

    await waitFor(() => expect(screen.queryByPlaceholderText("Find...")).not.toBeInTheDocument());
    expect(onFindAndReplace).toHaveBeenCalledTimes(1);
    expect(document.activeElement).toBe(button("Find & Replace"));
  });

  /** Starts Replace All from the keyboard, as Chrome runs it: the button blurs as it disables. */
  const startReplaceAll = async () => {
    let finish = () => {};
    const onFindAndReplace = vi.fn(() => new Promise<void>((resolve) => (finish = resolve)));
    renderView({ onFindAndReplace });
    fireEvent.click(button("Find & Replace"));
    fireEvent.change(findField(), { target: { value: "alpha" } });
    const replaceAll = button(/Replace All/);
    replaceAll.focus();
    fireEvent.click(replaceAll);
    expect(replaceAll).toBeDisabled();
    fireEvent.focusOut(replaceAll);
    // Let the toolbar settle that focus-out before the next step.
    await act(async () => {});
    return () => act(async () => finish());
  };

  it("sends focus to Find & Replace when Replace All finishes with focus still in the row", async () => {
    const finish = await startReplaceAll();
    expect(document.activeElement).toBe(findField());

    await finish();

    expect(screen.queryByPlaceholderText("Find...")).not.toBeInTheDocument();
    expect(document.activeElement).toBe(button("Find & Replace"));
  });

  it("sends focus to Find & Replace when the row is blurred as it is removed, as Chrome does", async () => {
    // Chrome blurs a focused element just before removing it: focus is
    // already on the page when its focusout fires. jsdom doesn't, so removal
    // is wrapped to do the same here.
    const removeChild = Node.prototype.removeChild;
    const spy = vi.spyOn(Node.prototype, "removeChild").mockImplementation(function <T extends Node>(
      this: Node,
      child: T,
    ): T {
      const focused = document.activeElement;
      if (focused instanceof HTMLElement && child.contains(focused)) focused.blur();
      return removeChild.call(this, child) as T;
    });
    try {
      const finish = await startReplaceAll();
      await finish();
      expect(document.activeElement).toBe(button("Find & Replace"));
    } finally {
      spy.mockRestore();
    }
  });

  it("sends focus to Find & Replace when Replace All was reached from outside the row", async () => {
    let finish = () => {};
    renderView({ onFindAndReplace: () => new Promise<void>((resolve) => (finish = resolve)) });
    fireEvent.click(button("Find & Replace"));
    fireEvent.change(findField(), { target: { value: "alpha" } });
    // Focus leaves the row for the page, then comes back to Replace All.
    findField().blur();
    await act(async () => {});
    const replaceAll = button(/Replace All/);
    replaceAll.focus();
    fireEvent.click(replaceAll);

    await act(async () => finish());

    expect(document.activeElement).toBe(button("Find & Replace"));
  });

  it("leaves focus alone when the user tabbed out of the row, then clicked the page, while Replace All saved", async () => {
    const finish = await startReplaceAll();
    button("Undo").focus();
    button("Undo").blur();
    expect(document.activeElement).toBe(document.body);

    await finish();

    expect(document.activeElement).toBe(document.body);
  });

  it("leaves focus alone when the user left the row while Replace All saved", async () => {
    const finish = await startReplaceAll();
    // A click on blank page takes focus from the find field to the page.
    findField().blur();
    expect(document.activeElement).toBe(document.body);

    await finish();

    expect(screen.queryByPlaceholderText("Find...")).not.toBeInTheDocument();
    expect(document.activeElement).toBe(document.body);
  });

  it("says whether Search and Find & Replace are on", () => {
    renderView();
    expect(button("Search")).toHaveAttribute("aria-pressed", "false");
    expect(button("Find & Replace")).toHaveAttribute("aria-pressed", "false");

    fireEvent.click(button("Search"));
    expect(button("Search")).toHaveAttribute("aria-pressed", "true");
    expect(button("Find & Replace")).toHaveAttribute("aria-pressed", "false");

    // Find & Replace opens the find field as well, so both are on.
    fireEvent.click(button("Find & Replace"));
    expect(button("Search")).toHaveAttribute("aria-pressed", "true");
    expect(button("Find & Replace")).toHaveAttribute("aria-pressed", "true");

    fireEvent.click(button("Find & Replace"));
    expect(button("Search")).toHaveAttribute("aria-pressed", "false");
    expect(button("Find & Replace")).toHaveAttribute("aria-pressed", "false");
    expect(screen.queryByPlaceholderText("Find...")).not.toBeInTheDocument();
  });

  it("closes the settings menu on Escape and returns focus to its toggle", () => {
    renderView();
    openEverything();
    const toggle = button("Advanced Search Settings");
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    const caseSensitive = screen.getByRole("checkbox", { name: "Case Sensitive" });
    expect(toggle).toHaveAttribute("aria-controls", caseSensitive.closest("[id]")?.id);

    caseSensitive.focus();
    fireEvent.keyDown(caseSensitive, { key: "Escape" });

    expect(settingsOpen()).toBe(false);
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(toggle).toHaveFocus();
  });

  it("closes the settings menu on Escape without taking focus from the find field", () => {
    renderView();
    openEverything();
    findField().focus();

    fireEvent.keyDown(findField(), { key: "Escape" });

    expect(settingsOpen()).toBe(false);
    expect(findField()).toHaveFocus();
  });

  it("keeps the settings menu open for an Escape that ends a composition or was handled", () => {
    renderView();
    openEverything();
    const caseSensitive = screen.getByRole("checkbox", { name: "Case Sensitive" });

    fireEvent.keyDown(caseSensitive, { key: "Escape", isComposing: true });
    expect(settingsOpen()).toBe(true);

    caseSensitive.addEventListener("keydown", (event) => event.preventDefault());
    fireEvent.keyDown(caseSensitive, { key: "Escape" });
    expect(settingsOpen()).toBe(true);
  });

  it("opens Find & Replace again with the settings menu closed", () => {
    renderView();
    openEverything();
    expect(settingsOpen()).toBe(true);

    fireEvent.click(button("Find & Replace"));
    fireEvent.click(button("Find & Replace"));

    expect(settingsOpen()).toBe(false);
    expect(button("Advanced Search Settings")).toHaveAttribute("aria-expanded", "false");
  });
});

describe("transcript play buttons", () => {
  it("name each group's button by speaker and time, ring it and give it a full target", () => {
    renderView();

    for (const name of ["Play Alex at 00:00", "Play Blair at 01:05"]) {
      const control = button(name);
      expect(control.className).toContain(FOCUS_RING);
      expectIconTarget(control, name);
    }
  });

  it("tell apart two groups that start in the same second", () => {
    renderView({
      segments: [
        { id: "u0", speaker: "SPEAKER_0", start: 65.1, end: 65.5, text: "Yes." },
        { id: "u1", speaker: "SPEAKER_1", start: 65.7, end: 68, text: "Then we agree." },
        { id: "u2", speaker: "SPEAKER_0", start: 70, end: 74, text: "Both of us" },
        { id: "u3", speaker: "SPEAKER_1", start: 71, end: 73, text: "at once." },
      ],
    });

    const names = screen
      .getAllByRole("button")
      .map((control) => control.getAttribute("aria-label") ?? "")
      .filter((name) => name.startsWith("Play "));
    expect(names).toEqual(["Play Alex at 01:05", "Play Blair at 01:05", "Play Alex and Blair at 01:10"]);
  });

  it("names the playing group's button Pause and pauses it", () => {
    const onPause = vi.fn();
    renderView({ currentTime: 1, isPlaying: true, onPause });

    fireEvent.click(button("Pause Alex at 00:00"));

    expect(onPause).toHaveBeenCalledTimes(1);
    expect(button("Play Blair at 01:05")).toBeInTheDocument();
  });
});
