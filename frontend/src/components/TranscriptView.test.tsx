import { fireEvent, render, screen } from "@testing-library/react";
import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import TranscriptView from "./TranscriptView";
import type { TranscriptSegment } from "@/types";

const addNotification = vi.fn();
const positionMap = new Map<string, { top: number; height: number }>();
const originalOffsetTop = Object.getOwnPropertyDescriptor(
  HTMLElement.prototype,
  "offsetTop",
);
const originalOffsetHeight = Object.getOwnPropertyDescriptor(
  HTMLElement.prototype,
  "offsetHeight",
);
const originalScrollIntoView = Element.prototype.scrollIntoView;

vi.mock("@/lib/notificationStore", () => ({
  useNotificationStore: () => ({ addNotification }),
}));

function buildSegment(
  overrides: Partial<TranscriptSegment> & Pick<TranscriptSegment, "id" | "text" | "speaker">,
): TranscriptSegment {
  return {
    id: overrides.id,
    start: overrides.start ?? 0,
    end: overrides.end ?? 1,
    text: overrides.text,
    speaker: overrides.speaker,
    revision: overrides.revision ?? 1,
    speaker_state: overrides.speaker_state,
    provisional: overrides.provisional,
    overlapping_speakers: overrides.overlapping_speakers,
    speaker_manually_edited: overrides.speaker_manually_edited,
    speaker_confidence: overrides.speaker_confidence,
    updated_at: overrides.updated_at,
    state: overrides.state,
  };
}

function renderTranscriptView(
  segments: TranscriptSegment[],
  overrides: Partial<React.ComponentProps<typeof TranscriptView>> = {},
) {
  return render(
    <TranscriptView
      recordingId="rec-1"
      segments={segments}
      currentTime={999}
      onPlaySegment={vi.fn()}
      isPlaying={false}
      onPause={vi.fn()}
      onResume={vi.fn()}
      speakerMap={{
        SPEAKER_A: "Alex",
        SPEAKER_B: "Blair",
        SPEAKER_00: "Dana",
      }}
      speakers={[]}
      globalSpeakers={[]}
      onRenameSpeaker={vi.fn()}
      onUpdateSegmentSpeaker={vi.fn()}
      onUpdateSegmentText={vi.fn()}
      onFindAndReplace={vi.fn()}
      speakerColors={{
        SPEAKER_A: "orange",
        SPEAKER_B: "blue",
        SPEAKER_00: "green",
      }}
      onUndo={vi.fn()}
      onRedo={vi.fn()}
      canUndo={false}
      canRedo={false}
      onExport={vi.fn()}
      {...overrides}
    />,
  );
}

beforeAll(() => {
  Object.defineProperty(HTMLElement.prototype, "offsetTop", {
    configurable: true,
    get() {
      const segmentId = (this as HTMLElement).dataset.segmentId;
      return segmentId ? (positionMap.get(segmentId)?.top ?? 0) : 0;
    },
  });

  Object.defineProperty(HTMLElement.prototype, "offsetHeight", {
    configurable: true,
    get() {
      const segmentId = (this as HTMLElement).dataset.segmentId;
      return segmentId ? (positionMap.get(segmentId)?.height ?? 40) : 40;
    },
  });

  Element.prototype.scrollIntoView = vi.fn();
});

afterAll(() => {
  if (originalOffsetTop) {
    Object.defineProperty(HTMLElement.prototype, "offsetTop", originalOffsetTop);
  }
  if (originalOffsetHeight) {
    Object.defineProperty(
      HTMLElement.prototype,
      "offsetHeight",
      originalOffsetHeight,
    );
  }
  Element.prototype.scrollIntoView = originalScrollIntoView;
});

describe("TranscriptView", () => {
  beforeEach(() => {
    addNotification.mockReset();
    positionMap.clear();
  });

  it("disables export while a local edit is open", () => {
    renderTranscriptView([
      buildSegment({
        id: "utt-stable",
        text: "Stable text",
        speaker: "SPEAKER_00",
        speaker_state: "stable",
      }),
    ]);

    expect(screen.queryByText("Stable speaker")).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Export transcript" }),
    ).toBeEnabled();

    fireEvent.click(screen.getByText("Stable text"));

    expect(
      screen.getByRole("button", { name: "Export transcript disabled" }),
    ).toBeDisabled();
  });

  it("keeps overlap lanes in transcript order and reserves space for revised overlap groups", () => {
    const now = new Date().toISOString();

    const { container } = renderTranscriptView([
      buildSegment({
        id: "lead-b",
        text: "Blair opens",
        speaker: "SPEAKER_B",
        start: 0,
        end: 1,
        speaker_state: "stable",
      }),
      buildSegment({
        id: "overlap-b",
        text: "Blair overlap",
        speaker: "SPEAKER_B",
        start: 10,
        end: 12,
        overlapping_speakers: ["SPEAKER_A"],
        updated_at: now,
      }),
      buildSegment({
        id: "overlap-a",
        text: "Alex overlap",
        speaker: "SPEAKER_A",
        start: 10.5,
        end: 11.5,
        overlapping_speakers: ["SPEAKER_B"],
        updated_at: now,
      }),
    ]);

    const laneOrder = Array.from(
      container.querySelectorAll<HTMLElement>(
        "[data-testid^='overlap-lane-']:not([data-testid*='body'])",
      ),
    ).map((element) => element.dataset.testid || element.getAttribute("data-testid"));

    expect(laneOrder).toEqual([
      "overlap-lane-SPEAKER_B",
      "overlap-lane-SPEAKER_A",
    ]);
    expect(screen.getByTestId("overlap-lane-body-SPEAKER_B")).toHaveStyle({
      minHeight: "7rem",
    });
  });

  it("preserves scroll position when older utterances are inserted ahead of the current viewport", () => {
    positionMap.set("utt-1", { top: 0, height: 40 });
    positionMap.set("utt-2", { top: 80, height: 40 });
    positionMap.set("utt-3", { top: 160, height: 40 });

    const { rerender } = renderTranscriptView([
      buildSegment({ id: "utt-1", text: "First", speaker: "SPEAKER_00", start: 0, end: 1 }),
      buildSegment({ id: "utt-2", text: "Second", speaker: "SPEAKER_00", start: 2, end: 3 }),
      buildSegment({ id: "utt-3", text: "Third", speaker: "SPEAKER_00", start: 4, end: 5 }),
    ]);

    const scrollRegion = screen.getByTestId(
      "transcript-scroll-region",
    ) as HTMLDivElement;
    Object.defineProperty(scrollRegion, "scrollTop", {
      configurable: true,
      writable: true,
      value: 50,
    });

    fireEvent.scroll(scrollRegion);

    positionMap.set("utt-1", { top: 0, height: 40 });
    positionMap.set("utt-new", { top: 40, height: 40 });
    positionMap.set("utt-2", { top: 120, height: 40 });
    positionMap.set("utt-3", { top: 200, height: 40 });

    rerender(
      <TranscriptView
        recordingId="rec-1"
        segments={[
          buildSegment({ id: "utt-1", text: "First", speaker: "SPEAKER_00", start: 0, end: 1 }),
          buildSegment({ id: "utt-new", text: "Inserted", speaker: "SPEAKER_00", start: 1, end: 1.5 }),
          buildSegment({ id: "utt-2", text: "Second", speaker: "SPEAKER_00", start: 2, end: 3 }),
          buildSegment({ id: "utt-3", text: "Third", speaker: "SPEAKER_00", start: 4, end: 5 }),
        ]}
        currentTime={999}
        onPlaySegment={vi.fn()}
        isPlaying={false}
        onPause={vi.fn()}
        onResume={vi.fn()}
        speakerMap={{ SPEAKER_00: "Dana" }}
        speakers={[]}
        globalSpeakers={[]}
        onRenameSpeaker={vi.fn()}
        onUpdateSegmentSpeaker={vi.fn()}
        onUpdateSegmentText={vi.fn()}
        onFindAndReplace={vi.fn()}
        speakerColors={{ SPEAKER_00: "green" }}
        onUndo={vi.fn()}
        onRedo={vi.fn()}
        canUndo={false}
        canRedo={false}
        onExport={vi.fn()}
      />,
    );

    expect(scrollRegion.scrollTop).toBe(90);
  });
});