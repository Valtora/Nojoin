import type { LiveTranscriptWord } from "@/types";

/** A canonical word's exact character range in the displayed text. */
export interface WordRange {
  word: LiveTranscriptWord;
  from: number;
  to: number;
}

/**
 * Locate stored tokens without rebuilding spacing or guessing word times.
 * Missing/mismatched evidence falls back to the plain line; it must never
 * make different words look locked or send invented source identities.
 */
export function wordRanges(text: string, words: readonly LiveTranscriptWord[]): WordRange[] {
  const ranges: WordRange[] = [];
  let cursor = 0;
  for (const word of words) {
    const token = word.word.trim();
    if (!token) continue;
    const from = text.indexOf(token, cursor);
    if (from < 0 || text.slice(cursor, from).trim()) return [];
    ranges.push({ word, from, to: from + token.length });
    cursor = from + token.length;
  }
  return text.slice(cursor).trim() ? [] : ranges;
}
