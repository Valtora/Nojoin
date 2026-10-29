import { describe, expect, it } from "vitest";
import { wordRanges } from "./wordRanges";
import type { LiveTranscriptWord } from "@/types";

const words = (...tokens: string[]): LiveTranscriptWord[] => tokens.map((word, order) => ({
  word, order, id: `w${order}`, locked: true, start_ms: null, end_ms: null,
}));

describe("canonical word ranges", () => {
  it("locates repeated tokens, contractions, joined punctuation and Unicode without changing text", () => {
    const text = "I can't, café café!";
    const ranges = wordRanges(text, words("I", " can", "'t", ",", " café", " café", "!"));
    expect(ranges.map(({ from, to }) => text.slice(from, to))).toEqual(["I", "can", "'t", ",", "café", "café", "!"]);
    expect(ranges.map(({ from, to }) => [from, to])).toEqual([[0, 1], [2, 5], [5, 7], [7, 8], [9, 13], [14, 18], [18, 19]]);
  });

  it("refuses partial or mismatched evidence instead of marking unrelated text locked", () => {
    expect(wordRanges("Missing prefix word", words("word"))).toEqual([]);
    expect(wordRanges("One more", words("One"))).toEqual([]);
    expect(wordRanges("One", words("Other"))).toEqual([]);
  });
});
