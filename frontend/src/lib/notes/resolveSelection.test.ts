import { describe, expect, it } from "vitest";

import {
  ANCHOR_MARGIN_MS,
  ANCHOR_SCORE_FLOOR,
  resolveSelection,
  type Resolution,
} from "./resolveSelection";
import { interpolatedTokens, type DrawnLine } from "./selectionRange";

/** A line from `startMs`, 100 ms a character, so a word's time is easy to read off. */
const lineAt = (segmentId: string, startMs: number, text: string): DrawnLine => ({
  segmentId,
  text,
  startMs,
  endMs: startMs + 100 * text.length,
});

/** A note on characters `from`..`to` of `line`, timed as a selection there is (B3c). */
const noteOn = (line: DrawnLine, from: number, to: number, quote = line.text.slice(from, to)) => {
  const words = interpolatedTokens(line.text, line.startMs, line.endMs).filter(
    (token) => token.from < to && token.to > from,
  );
  return { start_ms: words[0].startMs, end_ms: words[words.length - 1].endMs, quoted_text: quote };
};

/** The marked text, line by line. */
const marked = (resolution: Resolution, lines: readonly DrawnLine[]) =>
  resolution.ranges.map(({ segmentId, from, to }) => [
    segmentId,
    lines.find((line) => line.segmentId === segmentId)!.text.slice(from, to),
  ]);

const VENUE = lineAt("u1", 60_000, "We need forty thousand for the venue, and soon.");

describe("resolveSelection", () => {
  it("finds the quote as it was written: exact, on its words", () => {
    const note = noteOn(VENUE, 8, 36);
    const resolution = resolveSelection(note, [VENUE]);
    expect(resolution.state).toBe("exact");
    expect(resolution.score).toBe(1);
    expect(marked(resolution, [VENUE])).toEqual([["u1", "forty thousand for the venue,"]]);
  });

  it("reads past case and punctuation as the same words", () => {
    const now = lineAt("f1", 60_000, "We need Forty-thousand... no: forty thousand, for the VENUE and soon");
    const resolution = resolveSelection({ ...noteOn(VENUE, 8, 36), quoted_text: "forty thousand for the venue" }, [now]);
    expect(resolution.state).toBe("exact");
    expect(marked(resolution, [now])).toEqual([["f1", "forty thousand, for the VENUE"]]);
  });

  it("finds the words after the final pass changed some: fuzzy, marked over the change", () => {
    const note = noteOn(VENUE, 0, VENUE.text.length);
    const now = lineAt("f1", 60_200, "We need fourteen thousand for the venue and soon.");
    const resolution = resolveSelection(note, [now]);
    expect(resolution.state).toBe("fuzzy");
    expect(resolution.score).toBeCloseTo(1 - 1 / 9);
    expect(marked(resolution, [now])).toEqual([["f1", now.text]]);
    // However long the quote, one word changed is never exact.
    const long = lineAt("u2", 60_000, "We need forty thousand for the venue, and soon, before the spring.");
    const longNow = lineAt("f2", 60_000, "We need fourteen thousand for the venue, and soon, before the spring.");
    expect(resolveSelection(noteOn(long, 0, long.text.length), [longNow])).toMatchObject({
      state: "fuzzy",
      score: 1 - 1 / 12,
    });
  });

  it("finds words an ASR pass split, merged or dropped, while enough of them are left", () => {
    // "gonna" → "going to" is two edits; "the whole" lost "whole", one more: three in ten words.
    const live = lineAt("l1", 10_000, "so we're gonna ask the whole team about the venue");
    const final = lineAt("f1", 10_000, "so we're going to ask the team about the venue");
    const resolution = resolveSelection(noteOn(live, 0, live.text.length), [final]);
    expect(resolution.state).toBe("fuzzy");
    expect(resolution.score).toBeCloseTo(1 - 3 / 10);
    expect(marked(resolution, [final])).toEqual([["f1", final.text]]);
  });

  it("finds a quote over two lines, however the new pass cut them, one range a line", () => {
    const first = lineAt("l1", 0, "the budget is forty thousand");
    const second = lineAt("l2", first.endMs, "for the venue in March");
    const note = {
      start_ms: noteOn(first, 14, 28).start_ms,
      end_ms: noteOn(second, 0, 13).end_ms,
      quoted_text: "forty thousand for the venue",
    };
    const recut = [lineAt("f1", 0, "the budget is forty"), lineAt("f2", 1_900, "thousand for the venue in March")];
    const resolution = resolveSelection(note, recut);
    expect(resolution.state).toBe("exact");
    expect(marked(resolution, recut)).toEqual([
      ["f1", "forty"],
      ["f2", "thousand for the venue"],
    ]);
  });

  it("never looks past the margin: the quote further away than that is unanchored", () => {
    const note = noteOn(VENUE, 8, 13); // "forty", 60.8 s to 61.3 s
    // Said earlier than the span, on a line of its own that ends 500 ms after it starts.
    const earlier = (startMs: number) => [
      lineAt("f1", startMs, "forty"),
      lineAt("f2", 60_000, "We need a budget"),
    ];
    // It ends just inside the margin before the span, then just outside it.
    const lineStartAtMargin = note.start_ms - ANCHOR_MARGIN_MS - 500;
    const inside = resolveSelection(note, earlier(lineStartAtMargin));
    expect(inside.state).toBe("exact");
    const outside = resolveSelection(note, earlier(lineStartAtMargin - 1));
    expect(outside.state).toBe("unanchored");
    expect(outside.ranges).toEqual([]);
    // And after it: it starts just inside the margin after the span, then just outside.
    const later = (startMs: number) => [lineAt("f1", 60_000, "We need a budget"), lineAt("f2", startMs, "forty")];
    expect(resolveSelection(note, later(note.end_ms + ANCHOR_MARGIN_MS)).state).toBe("exact");
    expect(resolveSelection(note, later(note.end_ms + ANCHOR_MARGIN_MS + 1)).state).toBe("unanchored");
    // Two and a half seconds after the span is too far.
    expect(resolveSelection(note, later(note.end_ms + 2_500)).state).toBe("unanchored");
    expect(resolveSelection(note, later(note.end_ms + 1_500)).state).toBe("exact");
  });

  it("shows what an unanchored note's span reads now, without marking it", () => {
    const note = noteOn(VENUE, 8, 22);
    const now = [lineAt("f1", 60_000, "We need a much bigger budget than that")];
    const resolution = resolveSelection(note, now);
    expect(resolution.state).toBe("unanchored");
    expect(resolution.ranges).toEqual([]);
    // Every word whose time touches 60.8 s to 62.2 s.
    expect(resolution.bestGuess.map(({ from, to }) => now[0].text.slice(from, to))).toEqual([
      "a much bigger budget",
    ]);
  });

  it("puts a phrase said twice in the span on the one nearest the note's time", () => {
    // Three times in 5.6 s, all well inside the margin of each other.
    const line = lineAt("u1", 0, "yes we can, I said yes we can, and then yes we can again");
    const second = line.text.indexOf("yes we can", 15);
    const resolution = resolveSelection(noteOn(line, second, second + 10), [line]);
    expect(resolution.state).toBe("exact");
    // A mark runs to the end of a word's characters, its comma too.
    expect(resolution.ranges).toEqual([{ segmentId: "u1", from: second, to: second + 11 }]);
    const first = resolveSelection(noteOn(line, 0, 10), [line]);
    expect(first.ranges).toEqual([{ segmentId: "u1", from: 0, to: 11 }]);
    const third = line.text.lastIndexOf("yes we can");
    expect(resolveSelection(noteOn(line, third, third + 10), [line]).ranges).toEqual([
      { segmentId: "u1", from: third, to: third + 10 },
    ]);
  });

  it("leaves an empty quote, or one of punctuation only, unanchored", () => {
    for (const quoted_text of ["", "   ", "… —", null]) {
      const resolution = resolveSelection({ ...noteOn(VENUE, 8, 22), quoted_text }, [VENUE]);
      expect(resolution).toMatchObject({ state: "unanchored", ranges: [], score: 0 });
    }
  });

  it("finds a one-word quote only as that word: one changed word is not a fuzzy match", () => {
    expect(resolveSelection(noteOn(VENUE, 8, 13), [VENUE]).state).toBe("exact");
    const changed = lineAt("f1", 60_000, "We need fourty thousand for the venue, and soon.");
    expect(resolveSelection(noteOn(VENUE, 8, 13), [changed])).toMatchObject({ state: "unanchored", ranges: [] });
  });

  it("marks a match at the floor, and none under it", () => {
    expect(ANCHOR_SCORE_FLOOR).toBe(0.5);
    // Two words, one heard differently: half of them, at the floor.
    const atFloor = resolveSelection(noteOn(VENUE, 8, 22), [lineAt("f1", 60_000, "We need forty grand for the venue")]);
    expect(atFloor.state).toBe("fuzzy");
    expect(atFloor.score).toBeCloseTo(0.5);
    expect(marked(atFloor, [lineAt("f1", 60_000, "We need forty grand for the venue")])[0][1]).toMatch(/^forty/);
    // Five words, three changed: under it.
    const quote = noteOn(VENUE, 8, 36);
    const threeChanged = lineAt("f1", 60_000, "We need forty grand to a venue, and soon.");
    const under = resolveSelection(quote, [threeChanged]);
    expect(under.score).toBeCloseTo(0.4);
    expect(under).toMatchObject({ state: "unanchored", ranges: [] });
  });

  it("follows the floor and margin it is given", () => {
    const quote = noteOn(VENUE, 8, 36);
    const twoChanged = lineAt("f1", 60_000, "We need forty grand for a venue, and soon.");
    expect(resolveSelection(quote, [twoChanged]).score).toBeCloseTo(0.6);
    expect(resolveSelection(quote, [twoChanged], { floor: 0.61 }).state).toBe("unanchored");
    // "forty" ends 5 s before the span starts.
    const far = [lineAt("f2", 60_800 - 5_000 - 500, "forty thousand")];
    expect(resolveSelection(noteOn(VENUE, 8, 22), far, { marginMs: 5_000 }).state).toBe("exact");
    expect(resolveSelection(noteOn(VENUE, 8, 22), far).state).toBe("unanchored");
  });
});

/** A small seeded generator, so a failing case can be run again. */
const random = (seed: number) => () => {
  seed = (seed * 1_103_515_245 + 12_345) % 2_147_483_648;
  return seed / 2_147_483_648;
};

const WORDS = [
  "the", "budget", "venue", "forty", "thousand", "march", "we", "need", "council", "agreed",
  "motion", "member", "question", "yes", "no", "and", "a", "to", "of", "report", "minister",
  "noble", "lord", "safety", "water", "housing", "police", "transport", "time", "please",
];

const PUNCTUATION = ["", "", "", ",", ".", "?", "!", ";", "…"];

describe("resolveSelection on random transcripts", () => {
  /**
   * A transcript of short lines, one word every 300-500 ms, and the same words
   * cut into lines elsewhere, with punctuation and case changed: what a second
   * pass over the same audio gives when it hears the same words.
   */
  const transcripts = (next: () => number) => {
    const pick = <T,>(list: readonly T[]) => list[Math.floor(next() * list.length)];
    const words = Array.from({ length: 40 + Math.floor(next() * 40) }, () => pick(WORDS));
    const times: number[] = [];
    let at = 1_000 + Math.floor(next() * 5_000);
    for (let index = 0; index < words.length; index += 1) {
      times.push(at);
      at += 300 + Math.floor(next() * 200);
    }
    const cut = (variant: (word: string) => string): DrawnLine[] => {
      const lines: DrawnLine[] = [];
      let start = 0;
      while (start < words.length) {
        const end = Math.min(words.length, start + 3 + Math.floor(next() * 8));
        lines.push({
          segmentId: `s${lines.length}`,
          text: words.slice(start, end).map(variant).join(" "),
          startMs: times[start],
          endMs: times[end - 1] + 300,
        });
        start = end;
      }
      return lines;
    };
    const then = cut((word) => word);
    const now = cut((word) => {
      const cased = next() < 0.3 ? word.toUpperCase() : next() < 0.3 ? word[0].toUpperCase() + word.slice(1) : word;
      return cased + pick(PUNCTUATION);
    });
    return { then, now, pick };
  };

  /** A selection of a few words inside one line of `then`, as B3c makes it. */
  const select = (then: readonly DrawnLine[], next: () => number) => {
    const line = then[Math.floor(next() * then.length)];
    const tokens = interpolatedTokens(line.text, line.startMs, line.endMs);
    const count = 2 + Math.floor(next() * Math.min(5, tokens.length - 1));
    const first = Math.floor(next() * (tokens.length - count + 1));
    const last = Math.min(tokens.length, first + count) - 1;
    return noteOn(line, tokens[first].from, tokens[last].to);
  };

  it("finds the quote, exactly or with changes, whatever the re-cut, case and punctuation (5,000 cases)", () => {
    const next = random(488);
    for (let run = 0; run < 5_000; run += 1) {
      const { then, now } = transcripts(next);
      const note = select(then, next);
      const resolution = resolveSelection(note, now);
      expect(resolution.state, JSON.stringify({ run, note })).not.toBe("unanchored");
    }
  });

  it("never marks words when the quoted ones are gone from near its span (5,000 cases)", () => {
    const next = random(3_888);
    for (let run = 0; run < 5_000; run += 1) {
      const { then, now } = transcripts(next);
      const note = select(then, next);
      // Near the note the new text is other words, from a list the quote never
      // uses; further away it still says the same, quote and all.
      const others = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot"];
      const near = (line: DrawnLine) =>
        line.endMs >= note.start_ms - ANCHOR_MARGIN_MS - 1_000 &&
        line.startMs <= note.end_ms + ANCHOR_MARGIN_MS + 1_000;
      const replaced = now.map((line) =>
        near(line)
          ? {
              ...line,
              text: line.text
                .split(" ")
                .map((_, index) => others[(index + run) % others.length])
                .join(" "),
            }
          : line,
      );
      const resolution = resolveSelection(note, replaced);
      expect(resolution.ranges, JSON.stringify({ run, note })).toEqual([]);
      expect(resolution.state).toBe("unanchored");
    }
  });
});
