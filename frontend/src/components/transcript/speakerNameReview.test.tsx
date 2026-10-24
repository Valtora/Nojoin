import { act, fireEvent, render, screen, within } from "@testing-library/react";
import type { ComponentProps } from "react";
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";

import TranscriptView from "@/components/TranscriptView";
import type { RecordingSpeaker, TranscriptSegment } from "@/types";

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

const SEGMENTS: TranscriptSegment[] = [
  { id: "u0", speaker: "SPEAKER_A", start: 0, end: 2, text: "Line zero." },
  { id: "u1", speaker: "SPEAKER_B", start: 10, end: 12, text: "Line one." },
  { id: "u2", speaker: "SPEAKER_A", start: 20, end: 22, text: "Line two." },
];

const speaker = (overrides: Partial<RecordingSpeaker>): RecordingSpeaker =>
  ({
    id: 1,
    created_at: "2026-10-23T00:00:00Z",
    updated_at: "2026-10-23T00:00:00Z",
    recording_id: "rec-1",
    diarization_label: "SPEAKER_A",
    ...overrides,
  }) as RecordingSpeaker;

/** Alex's name is an AI suggestion; Blair's stands. */
const SPEAKERS = [
  speaker({ local_name: "Alex", name_source: "llm", name_pending: true }),
  speaker({ id: 2, diarization_label: "SPEAKER_B", local_name: "Blair", name_source: "user" }),
];

function renderView(overrides: Partial<ComponentProps<typeof TranscriptView>> = {}) {
  const onReviewSpeakerName = vi.fn(async () => {});
  render(
    <TranscriptView
      recordingId="rec-1"
      segments={SEGMENTS}
      currentTime={-1}
      onPlaySegment={vi.fn()}
      isPlaying={false}
      onPause={vi.fn()}
      onResume={vi.fn()}
      speakerMap={{ SPEAKER_A: "Alex", SPEAKER_B: "Blair" }}
      speakers={SPEAKERS}
      globalSpeakers={[]}
      onRenameSpeaker={vi.fn()}
      onUpdateSegmentSpeaker={vi.fn()}
      onReviewSpeakerName={onReviewSpeakerName}
      onUpdateSegmentText={vi.fn()}
      onFindAndReplace={vi.fn()}
      speakerColors={{}}
      onUndo={vi.fn()}
      onRedo={vi.fn()}
      canUndo={false}
      canRedo={false}
      onExport={vi.fn()}
      {...overrides}
    />,
  );
  return { onReviewSpeakerName };
}

const reviewButtons = () => screen.queryAllByRole("button", { name: /^Confirm or dismiss the name/ });
const lineOf = (element: Element) => element.closest("[data-order-index]")?.getAttribute("data-segment-id");

describe("suggested names on transcript lines", () => {
  it("puts a ? on the first line of the speaker whose name is pending, and no other", () => {
    renderView();

    expect(reviewButtons().map(lineOf)).toEqual(["u0"]);
    expect(reviewButtons()[0]).toHaveAccessibleName("Confirm or dismiss the name Alex");
  });

  it("moves the ? to the speaker's next line when the first is hidden", () => {
    renderView({ segments: [SEGMENTS[1], SEGMENTS[2]] });

    expect(reviewButtons().map(lineOf)).toEqual(["u2"]);
  });

  it("offers nothing on a read-only transcript", () => {
    renderView({ readOnly: true });

    expect(reviewButtons()).toEqual([]);
  });

  it("offers nothing on a provisional line, even one that can be edited", () => {
    renderView({
      segments: [{ ...SEGMENTS[0], provisional: true }],
      allowProvisionalEdits: true,
    });

    expect(reviewButtons()).toEqual([]);
  });

  it("offers nothing for a speaker merged into another", () => {
    renderView({
      speakers: [speaker({ local_name: "Alex", name_pending: true, merged_into_id: 9 })],
    });

    expect(reviewButtons()).toEqual([]);
  });

  it("offers nothing where the page gives no way to review names", () => {
    renderView({ onReviewSpeakerName: undefined });

    expect(reviewButtons()).toEqual([]);
  });

  it("confirms the line's speaker from the ?'s popover", async () => {
    const { onReviewSpeakerName } = renderView();

    fireEvent.click(reviewButtons()[0]);
    const popover = screen.getByRole("dialog", { name: "Suggested name Alex" });
    expect(within(popover).getByText("Suggested by AI")).toBeInTheDocument();
    fireEvent.click(within(popover).getByRole("button", { name: "Confirm" }));
    await act(async () => {});

    expect(onReviewSpeakerName.mock.calls).toEqual([["SPEAKER_A", "Alex", "confirm"]]);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("dismisses the line's speaker's name with Not {name}", async () => {
    const { onReviewSpeakerName } = renderView();

    fireEvent.click(reviewButtons()[0]);
    fireEvent.click(screen.getByRole("button", { name: "Not Alex" }));
    await act(async () => {});

    expect(onReviewSpeakerName.mock.calls).toEqual([["SPEAKER_A", "Alex", "dismiss"]]);
  });

  it("ignores the ? while its decision is sent", async () => {
    let finish = () => {};
    const onReviewSpeakerName = vi.fn(() => new Promise<void>((resolve) => (finish = resolve)));
    renderView({ onReviewSpeakerName });

    fireEvent.click(reviewButtons()[0]);
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
    await act(async () => {});
    expect(reviewButtons()[0]).toHaveAttribute("aria-busy", "true");
    fireEvent.click(reviewButtons()[0]);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    await act(async () => finish());
    expect(reviewButtons()[0]).not.toHaveAttribute("aria-busy");
    expect(onReviewSpeakerName).toHaveBeenCalledTimes(1);
  });

  it("gives focus to the line's speaker name once the name is decided", async () => {
    renderView();

    fireEvent.click(reviewButtons()[0]);
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
    await act(async () => {});

    const line = document.querySelector('[data-segment-id="u0"]') as HTMLElement;
    expect(within(line).getByRole("button", { name: "Alex" })).toHaveFocus();
  });

  it("closes when focus leaves the popover, and not while it stays inside", () => {
    renderView();

    fireEvent.click(reviewButtons()[0]);
    const popover = screen.getByRole("dialog", { name: "Suggested name Alex" });
    const confirm = within(popover).getByRole("button", { name: "Confirm" });
    const notAlex = within(popover).getByRole("button", { name: "Not Alex" });
    fireEvent.focusOut(confirm, { relatedTarget: notAlex });
    expect(screen.getByRole("dialog")).toBeInTheDocument();

    fireEvent.focusOut(notAlex, { relatedTarget: document.body });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("stays open on a blur with nowhere to go, which a press outside closes instead", () => {
    // Safari blurs to nothing on a press of a button; closing then would let
    // that press of the "?" open the popover again.
    renderView();

    fireEvent.click(reviewButtons()[0]);
    const popover = screen.getByRole("dialog", { name: "Suggested name Alex" });
    fireEvent.focusOut(within(popover).getByRole("button", { name: "Confirm" }), {
      relatedTarget: null,
    });
    expect(screen.getByRole("dialog")).toBeInTheDocument();

    fireEvent.pointerDown(document.body);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("shows only the buttons when the source has no reason to give", () => {
    renderView({
      speakers: [speaker({ local_name: "Alex", name_source: null, name_pending: true })],
    });

    fireEvent.click(reviewButtons()[0]);
    const popover = screen.getByRole("dialog", { name: "Suggested name Alex" });

    expect(within(popover).queryByRole("paragraph")).not.toBeInTheDocument();
    expect(popover.querySelectorAll("p")).toHaveLength(0);
    expect(within(popover).getAllByRole("button").map((b) => b.textContent)).toEqual([
      "Confirm",
      "Not Alex",
    ]);
  });
});
