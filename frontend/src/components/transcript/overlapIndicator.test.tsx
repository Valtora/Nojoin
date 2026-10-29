import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import TranscriptView from "../TranscriptView";
import LiveTranscriptPanel from "../LiveTranscriptPanel";
import { buildTranscriptGroups, indexSegments } from "./transcriptGroups";
import { line, viewProps } from "./transcriptTestFixtures";
import { indexOverlaps } from "./OverlapIndicator";

vi.mock("@/lib/api", () => ({ getGlobalSpeakers: vi.fn().mockResolvedValue([]) }));

describe("overlap on the credited line", () => {
  it("excludes touching endpoints, zero-length atoms and same-speaker continuations", () => {
    const first = line({ end: 10 });
    const touching = line({ id: "touch", speaker: "B", start: 10, end: 12 });
    const point = line({ id: "point", speaker: "B", start: 5, end: 5 });
    const sameSpeaker = line({ id: "same", start: 6, end: 7 });
    expect(indexOverlaps([first, touching, point, sameSpeaker]).size).toBe(0);
  });
  it("treats a handover's slight overlap as no crosstalk, and a longer one as crosstalk", () => {
    const first = line({ end: 10 });
    const handover = line({ id: "handover", speaker: "B", start: 9.8, end: 12 });
    expect(indexOverlaps([first, handover]).size).toBe(0);
    const crosstalk = line({ id: "crosstalk", speaker: "B", start: 9.5, end: 12 });
    expect(indexOverlaps([first, crosstalk]).get(first)).toEqual([crosstalk]);
  });
  it("discloses metadata on hover, keyboard activation or touch click, without empty lanes", () => {
    const { container } = render(<TranscriptView {...viewProps([line({ overlapping_speakers: ["B"] })])} />);
    const trigger = screen.getByRole("button", { name: "Overlap" });
    expect(container.querySelector("[data-testid^='overlap-lane']")).toBeNull();
    expect(screen.queryByText("(overlapping speech)")).toBeNull();
    expect(screen.queryByRole("region", { name: "Overlapping speech" })).toBeNull();
    fireEvent.mouseEnter(trigger.parentElement!);
    expect(screen.getByRole("region", { name: "Overlapping speech" })).toHaveTextContent("Blair: No separately transcribed words.");
    fireEvent.mouseLeave(trigger.parentElement!);
    trigger.focus();
    fireEvent.click(trigger, { detail: 0 }); // Native button activation from Enter/Space.
    expect(trigger).toHaveAttribute("aria-expanded", "true");
    fireEvent.keyDown(trigger, { key: "Escape" });
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    fireEvent.pointerDown(trigger, { pointerType: "touch" });
    fireEvent.click(trigger);
    expect(trigger).toHaveAttribute("aria-expanded", "true");
    fireEvent.click(trigger);
    expect(trigger).toHaveAttribute("aria-expanded", "false");
  });

  it("quotes separately credited crosstalk and reattributes via the original line's menu", () => {
    const primary = line();
    const secondary = line({ id: "cross", speaker: "B", start: 12, end: 14, text: "Excuse me." });
    const props = viewProps([primary, secondary]);
    render(<TranscriptView {...props} />);
    fireEvent.click(screen.getAllByRole("button", { name: "Overlap" })[0]);
    expect(screen.getByRole("region", { name: "Overlapping speech" })).toHaveTextContent("Blair: Excuse me.");
    fireEvent.click(screen.getByRole("button", { name: "Actions for Alex's line at 00:00" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Change speaker" }));
    fireEvent.click(screen.getByRole("button", { name: /Blair Recording only/ }));
    expect(props.onUpdateSegmentSpeaker).toHaveBeenCalledWith(primary, {
      name: "Blair", diarizationLabel: "B", scope: "utterance_only",
    });
  });

  it("does not join distant events just because their secondary speakers match", () => {
    const segments = [line({ end: 3, overlapping_speakers: ["B"] }),
      line({ id: "later", start: 20, end: 23, overlapping_speakers: ["B"] })];
    expect(buildTranscriptGroups(indexSegments(segments)).map((group) => [group.start, group.end])).toEqual([[0, 3], [20, 23]]);
  });

  it("updates open crosstalk when a different credited line changes", () => {
    const primary = line();
    const secondary = line({ id: "cross", speaker: "B", start: 12, end: 14, text: "Earlier words" });
    const props = viewProps([primary, secondary]);
    const { rerender } = render(<TranscriptView {...props} />);
    fireEvent.click(screen.getAllByRole("button", { name: "Overlap" })[0]);
    rerender(<TranscriptView {...props} segments={[primary, { ...secondary, text: "Revised words" }]} />);
    expect(screen.getByRole("region", { name: "Overlapping speech" })).toHaveTextContent("Revised words");
    expect(screen.getByRole("region", { name: "Overlapping speech" })).not.toHaveTextContent("Earlier words");
  });

  it("shows the same disclosure and menu reattribution on locked live lines", async () => {
    const primary = line({ settling_required: true, locked: true, provisional: true, overlapping_speakers: ["B"] });
    const handlers = { editText: vi.fn(), changeSpeaker: vi.fn().mockResolvedValue(true), split: vi.fn() };
    render(<LiveTranscriptPanel segments={[primary]} hasLoaded lineHandlers={handlers}
      speakers={viewProps().speakers} onNoteOnLine={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Overlap" }));
    expect(screen.getByRole("region", { name: "Overlapping speech" })).toHaveTextContent("Blair:");
    fireEvent.click(screen.getByRole("button", { name: "Actions for the line at 00:00" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Change speaker" }));
    await act(async () => {});
    fireEvent.click(screen.getByRole("button", { name: /Blair Recording only/ }));
    expect(handlers.changeSpeaker).toHaveBeenCalledWith(primary, expect.objectContaining({ diarizationLabel: "B", scope: "utterance_only" }));
    expect(within(screen.getByTestId("live-transcript-line")).getByText(primary.text)).toBeInTheDocument();
  });
});
