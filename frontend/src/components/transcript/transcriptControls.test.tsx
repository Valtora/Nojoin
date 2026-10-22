import { fireEvent, render, screen } from "@testing-library/react";
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
// IconButton at sm: a 40 px box. jsdom has no layout, so the classes stand in.
const ICON_TARGET = ["h-10", "w-10"];
// The text buttons' 44 px on a touch screen.
const TEXT_TOUCH = "pointer-coarse:h-[44px]";

const SEGMENTS: TranscriptSegment[] = [
  { id: "u0", speaker: "SPEAKER_0", start: 0, end: 2, text: "alpha and beta" },
  { id: "u1", speaker: "SPEAKER_1", start: 65, end: 67, text: "beta alpha" },
];

function renderView(overrides: Partial<ComponentProps<typeof TranscriptView>> = {}) {
  return render(
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
    />,
  );
}

const button = (name: string | RegExp) => screen.getByRole("button", { name });

/** Opens Find & Replace with a term that matches, and the settings menu. */
function openEverything() {
  fireEvent.click(button("Find & Replace"));
  fireEvent.change(screen.getByPlaceholderText("Find..."), { target: { value: "alpha" } });
  fireEvent.click(button("Advanced Search Settings"));
}

describe("transcript toolbar", () => {
  it("names every icon button, rings it, and gives it a 40 px target", () => {
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
      for (const size of ICON_TARGET) expect(control.className.split(" "), name).toContain(size);
    }
  });

  it("rings Find Next, Replace and Replace All, with a touch target", () => {
    renderView();
    openEverything();

    for (const name of [/Find Next/, /^Replace$/, /Replace All/]) {
      const control = button(name);
      expect(control.className).toContain(FOCUS_RING);
      expect(control.className).toContain(TEXT_TOUCH);
    }
  });

  it("moves between matches with the named arrows", () => {
    renderView();
    fireEvent.click(button("Search"));
    expect(button("Previous match")).toBeDisabled();
    expect(button("Next match")).toBeDisabled();

    fireEvent.change(screen.getByPlaceholderText("Find..."), { target: { value: "alpha" } });
    expect(button("Next match")).toBeEnabled();

    fireEvent.click(button("Next match"));
    expect(screen.getByText("1 of 2")).toBeInTheDocument();
    fireEvent.click(button("Next match"));
    expect(screen.getByText("2 of 2")).toBeInTheDocument();
    fireEvent.click(button("Previous match"));
    expect(screen.getByText("1 of 2")).toBeInTheDocument();
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

    expect(screen.queryByRole("checkbox", { name: "Case Sensitive" })).not.toBeInTheDocument();
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(toggle).toHaveFocus();
  });

  it("closes the settings menu on Escape without taking focus from the find field", () => {
    renderView();
    openEverything();
    const find = screen.getByPlaceholderText("Find...");
    find.focus();

    fireEvent.keyDown(find, { key: "Escape" });

    expect(screen.queryByRole("checkbox", { name: "Case Sensitive" })).not.toBeInTheDocument();
    expect(find).toHaveFocus();
  });
});

describe("transcript play buttons", () => {
  it("name each group's button by its time, ring it and give it a 40 px target", () => {
    renderView();

    for (const name of ["Play segment at 00:00", "Play segment at 01:05"]) {
      const control = button(name);
      expect(control.className).toContain(FOCUS_RING);
      for (const size of ICON_TARGET) expect(control.className.split(" ")).toContain(size);
    }
  });

  it("names the playing group's button Pause and pauses it", () => {
    const onPause = vi.fn();
    renderView({ currentTime: 1, isPlaying: true, onPause });

    fireEvent.click(button("Pause segment at 00:00"));

    expect(onPause).toHaveBeenCalledTimes(1);
    expect(button("Play segment at 01:05")).toBeInTheDocument();
  });
});
