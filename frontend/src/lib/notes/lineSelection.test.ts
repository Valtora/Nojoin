import { describe, expect, it } from "vitest";

import { noteLength } from "./headNoteSync";
import { clampQuote, lineSelection } from "./lineSelection";
import { NOTE_QUOTE_MAX_CHARS } from "./noteOutbox";

describe("lineSelection", () => {
  it("takes the line's span in ms and its text, trimmed", () => {
    expect(lineSelection({ start: 12.5, end: 15.25, text: "  Forty thousand, then.\n" })).toEqual({
      startMs: 12_500,
      endMs: 15_250,
      quotedText: "Forty thousand, then.",
    });
  });

  it("prefers the line's own ms over its seconds", () => {
    expect(
      lineSelection({ start: 12, end: 15, start_ms: 12_345, end_ms: 15_678, text: "Hi" }),
    ).toEqual({ startMs: 12_345, endMs: 15_678, quotedText: "Hi" });
  });

  it.each([
    ["no text", { start: 1, end: 2, text: " \n " }],
    ["no span", { start: 2, end: 2, text: "Hi" }],
    ["a span that runs backwards", { start: 3, end: 2, text: "Hi" }],
  ])("gives none for a line with %s", (_name, segment) => {
    expect(lineSelection(segment)).toBeNull();
  });

  it("cuts a line longer than a note can quote, at the limit counted as the API counts", () => {
    const line = "😀".repeat(NOTE_QUOTE_MAX_CHARS + 5);
    const { quotedText } = lineSelection({ start: 0, end: 600, text: line })!;

    expect(noteLength(quotedText)).toBe(NOTE_QUOTE_MAX_CHARS);
    expect(quotedText.endsWith("…")).toBe(true);
    // Whole emoji only: no half of a surrogate pair is left at the cut.
    expect(quotedText.slice(0, -1)).toBe("😀".repeat(NOTE_QUOTE_MAX_CHARS - 1));
  });

  it("leaves a quote at the limit as it is", () => {
    const quote = "x".repeat(NOTE_QUOTE_MAX_CHARS);
    expect(clampQuote(quote)).toBe(quote);
  });
});
