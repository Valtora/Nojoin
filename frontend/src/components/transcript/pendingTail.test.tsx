import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import TranscriptView from "../TranscriptView";
import type { TimelineNote } from "@/types";
import { pendingTailLines } from "./pendingTailFixtures";
import { viewProps } from "./transcriptTestFixtures";

vi.mock("@/lib/api", async (original) => ({
  ...await original<typeof import("@/lib/api")>(), getGlobalSpeakers: vi.fn(async () => []),
}));

const bubbles = (container: HTMLElement) => container.querySelectorAll('[data-ui-reading="bubble"]');
const live = { hasLoaded: true };

describe("pending diarisation inside the previous utterance", () => {
  it.each([true, false])("keeps original inline paragraphs inside one actual bubble (live=%s)", (isLive) => {
    const segments = pendingTailLines();
    const { container } = render(<TranscriptView {...viewProps(segments)} live={isLive ? live : undefined}
      readOnly={!isLive} speakerColors={{ LIVE_PROVISIONAL_1: "blue", UNKNOWN: "green" }} />);
    expect(bubbles(container)).toHaveLength(1);
    const paragraphs = Array.from(container.querySelectorAll("p[data-segment-id]"));
    expect(paragraphs.map((p) => p.textContent)).toEqual(["Firm words", "pending tail"]);
    expect(paragraphs[0].closest('[data-ui-reading="bubble"]')).toBe(paragraphs[1].closest('[data-ui-reading="bubble"]'));
    expect(paragraphs[0]).toHaveClass("inline");
    expect(paragraphs[1]).toHaveClass("inline");
    expect(paragraphs[1]).toHaveAttribute("data-segment-id", "tail");
    expect(paragraphs[1]).toHaveAttribute("data-start-ms", "1000");
    expect(paragraphs[1].querySelector('[data-word-id="w3"]')).toHaveClass("italic");
    expect(container.querySelector('#segment-tail')).toHaveAttribute("data-order-index", "1");
    expect(bubbles(container)[0]).toHaveClass("bg-blue-100");
    expect(screen.queryByRole("button", { name: "UNKNOWN" })).not.toBeInTheDocument();
    expect(segments[1].speaker).toBe("UNKNOWN");
    expect(segments[1].locked).toBe(false);
  });

  it.each([true, false])("uses a neutral first pending bubble, including retained reads (live=%s)", (isLive) => {
    const [, tail] = pendingTailLines();
    const { container } = render(<TranscriptView {...viewProps([tail])} live={isLive ? live : undefined}
      speakerColors={{ UNKNOWN: "green" }} />);
    expect(bubbles(container)).toHaveLength(1);
    expect(bubbles(container)[0]).toHaveClass("bg-surface-inset", "border-surface-border");
    expect(bubbles(container)[0]).not.toHaveClass("bg-green-100");
    expect(screen.queryByRole("button", { name: "UNKNOWN" })).not.toBeInTheDocument();
  });

  it.each([true, false])("keeps unmarked pending live words italic (live=%s)", (isLive) => {
    const segments = pendingTailLines().map((segment) => ({ ...segment, settling_required: undefined, words: undefined }));
    const { container } = render(<TranscriptView {...viewProps(segments)} live={isLive ? live : undefined} />);
    expect(bubbles(container)).toHaveLength(1);
    expect(container.querySelector('p[data-segment-id="tail"]')).toHaveClass("inline", "italic", "text-contrast-helper");
  });

  it.each([
    { isLive: true, overlaps: false }, { isLive: false, overlaps: false },
    { isLive: true, overlaps: true }, { isLive: false, overlaps: true },
  ])("keeps a resumed pending tail separate (live=$isLive, overlap=$overlaps)", ({ isLive, overlaps }) => {
    const [anchor, tail] = pendingTailLines();
    const before = { ...anchor, end: overlaps ? 1.1 : anchor.end, capture_interval: 0 };
    const after = { ...tail, capture_interval: 1 };
    const props = { ...viewProps([before, after]), live: isLive ? live : undefined };
    const { container, rerender } = render(<TranscriptView {...props} />);
    expect(bubbles(container)).toHaveLength(2);
    const anchorBubble = container.querySelector('p[data-segment-id="anchor"]')!
      .closest('[data-ui-reading="bubble"]');
    expect(container.querySelector('p[data-segment-id="tail"]')!
      .closest('[data-ui-reading="bubble"]')).not.toBe(anchorBubble);

    // Pending speech in one resumed interval still shares its anchor's bubble.
    rerender(<TranscriptView {...props} segments={[{ ...before, capture_interval: 1 }, after]} />);
    expect(bubbles(container)).toHaveLength(1);
  });

  it("keeps covered UNKNOWN and source-channel changes separate", () => {
    const [anchor, tail] = pendingTailLines();
    const props = viewProps([anchor, tail]);
    const { container, rerender } = render(<TranscriptView {...props} live={live} />);
    expect(bubbles(container)).toHaveLength(1);
    rerender(<TranscriptView {...props} segments={[anchor, { ...tail, source_channel: "system" }]} live={live} />);
    expect(bubbles(container)).toHaveLength(2);
    expect(bubbles(container)[1]).toHaveClass("bg-surface-inset");
    expect(screen.getByText("Shared audio")).toBeInTheDocument();
    rerender(<TranscriptView {...props} segments={[anchor, { ...tail, diarisation_pending: false }]} live={live}
      speakerColors={{ UNKNOWN: "green" }} />);
    expect(bubbles(container)).toHaveLength(2);
    expect(bubbles(container)[1]).toHaveClass("bg-green-100");
  });

  it("follows authoritative same-speaker coalescing and keeps final reader behaviour unchanged", () => {
    const [anchor, tail] = pendingTailLines();
    const props = viewProps([anchor, tail]);
    const { container, rerender } = render(<TranscriptView {...props} live={live} />);
    const merged = { ...anchor, id: "merged", revision: 5, end: tail.end, text: `${anchor.text} ${tail.text}`,
      words: [...anchor.words!, ...tail.words!].map((word) => ({ ...word, locked: true })) };
    rerender(<TranscriptView {...props} segments={[merged]} live={live} />);
    expect(bubbles(container)).toHaveLength(1);
    expect(container.querySelector('p[data-segment-id="anchor"]')).toBeNull();
    expect(container.querySelector('p[data-segment-id="tail"]')).toBeNull();
    expect(container.querySelector('p[data-segment-id="merged"]')).toHaveTextContent("Firm words pending tail");
    expect(container.querySelector('[data-word-id="w3"]')).toHaveClass("not-italic");
    rerender(<TranscriptView {...props} segments={[anchor, { ...tail, speaker: "LIVE_PROVISIONAL_2", diarisation_pending: false }]}
      live={live} />);
    expect(bubbles(container)).toHaveLength(2);
    expect(screen.getByText("Speaker 2")).toBeInTheDocument();
    rerender(<TranscriptView {...props} segments={[{ ...anchor, provisional: false },
      { ...tail, diarisation_pending: undefined, provisional: false, locked: true }]} />);
    expect(bubbles(container)).toHaveLength(1);
    expect(container.querySelector('p[data-segment-id="tail"]')).toBeNull();
  });

  it("keeps a note in its original gap instead of losing it inside a new display group", () => {
    const [anchor, tail] = pendingTailLines();
    const note: TimelineNote = { id: "n1", recording_id: "r1", kind: "point", body: "Keep this gap",
      anchor_ms: 0, start_ms: 0, end_ms: 1000, revision: 1,
      created_at: "2026-10-27T12:00:00Z", updated_at: "2026-10-27T12:00:00Z" };
    const { container } = render(<TranscriptView {...viewProps([anchor, tail])} live={live} timelineNotes={[note]} />);
    expect(bubbles(container)).toHaveLength(2);
    expect(screen.getByText("Keep this gap")).toBeInTheDocument();
  });

  it.each(["text", "name"] as const)("keeps an open %s draft mounted while pending appears and splits", async (kind) => {
    const [anchor, tail] = pendingTailLines();
    const rename = vi.fn().mockResolvedValue(false);
    const editText = vi.fn().mockResolvedValue(false);
    const props = viewProps([anchor]);
    const presentation = { hasLoaded: true, onRenameLiveSpeaker: rename,
      lineHandlers: { editText, changeSpeaker: vi.fn(), split: vi.fn() } };
    const { container, rerender } = render(<TranscriptView {...props} live={presentation} />);
    if (kind === "name") fireEvent.click(screen.getByRole("button", { name: "Rename Speaker 1" }));
    else {
      fireEvent.click(screen.getByRole("button", { name: "Actions for the line at 00:00" }));
      fireEvent.click(screen.getByRole("menuitem", { name: "Edit text" }));
    }
    const editor = screen.getByRole(kind === "name" ? "combobox" : "textbox");
    fireEvent.change(editor, { target: { value: "Kept draft" } });
    rerender(<TranscriptView {...props} segments={[anchor, tail]} live={presentation} />);
    expect(bubbles(container)).toHaveLength(1);
    expect(screen.getByRole(kind === "name" ? "combobox" : "textbox")).toBe(editor);
    rerender(<TranscriptView {...props} segments={[{ ...anchor, revision: 8 },
      { ...tail, diarisation_pending: false, speaker: "LIVE_PROVISIONAL_2" }]} live={presentation} />);
    expect(bubbles(container)).toHaveLength(2);
    expect(editor).toHaveValue("Kept draft");
    expect(screen.getByRole(kind === "name" ? "combobox" : "textbox")).toBe(editor);
    if (kind === "name") fireEvent.keyDown(editor, { key: "Enter" });
    else fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(kind === "name" ? rename : editText).toHaveBeenCalledTimes(1));
    expect(kind === "name" ? rename : editText).toHaveBeenCalledWith(...(kind === "name"
      ? [anchor.speaker, "Kept draft"] : [anchor, "Kept draft"]));
  });
});
