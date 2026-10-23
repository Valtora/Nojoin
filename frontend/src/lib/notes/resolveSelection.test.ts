import { describe, expect, it } from "vitest";

import {
  ANCHOR_MARGIN_MS,
  ANCHOR_SCORE_FLOOR,
  PART_WORD_MIN_CHARS,
  comparableWord,
  resolveSelection,
  type LineRange,
  type Resolution,
} from "./resolveSelection";
import {
  interpolatedTokens,
  pickSpansInTimeOrder,
  selectionFromPicks,
  type DrawnLine,
  type LinePick,
} from "./selectionRange";

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

  it("finds a selection that starts or ends inside a word as exact, marked on the whole words", () => {
    // B3c quotes just the characters chosen: a drag from inside "thousand" to inside "venue".
    const from = VENUE.text.indexOf("ousand");
    const to = VENUE.text.indexOf("venue") + 2;
    const note = noteOn(VENUE, from, to);
    expect(note.quoted_text).toBe("ousand for the ve");
    const resolution = resolveSelection(note, [VENUE]);
    expect(resolution).toMatchObject({ state: "exact", score: 1 });
    expect(marked(resolution, [VENUE])).toEqual([["u1", "thousand for the venue,"]]);
    // Inside one word.
    const inside = resolveSelection(noteOn(VENUE, from, from + 4), [VENUE]);
    expect(inside.state).toBe("exact");
    expect(marked(inside, [VENUE])).toEqual([["u1", "thousand"]]);
  });

  it("finds a one-word quote inside a word only when it is three letters or more", () => {
    expect(PART_WORD_MIN_CHARS).toBe(3);
    // "a" was said, and the final pass heard "and" there instead: "and" is not marked.
    const live = lineAt("l1", 60_000, "We need a venue");
    const final = lineAt("f1", 60_000, "We need and venue");
    expect(resolveSelection(noteOn(live, 8, 9), [final])).toMatchObject({ state: "unanchored", ranges: [] });
    // A drag inside "forty": three of its letters are found there, two are not.
    const three = resolveSelection(noteOn(VENUE, 9, 12), [VENUE]);
    expect(three).toMatchObject({ state: "exact", score: 1 });
    expect(marked(three, [VENUE])).toEqual([["u1", "forty"]]);
    expect(resolveSelection(noteOn(VENUE, 9, 11), [VENUE])).toMatchObject({ state: "unanchored", ranges: [] });
  });

  it("takes part of a word only at the quote's ends: its first word's end, its last word's start", () => {
    const note = noteOn(VENUE, 8, 36);
    const as = (quoted_text: string) => resolveSelection({ ...note, quoted_text }, [VENUE]);
    // The start of a word is no first word, nor the end of one a last word.
    expect(as("fort thousand")).toMatchObject({ state: "fuzzy", score: 0.5 });
    expect(as("thousand enue")).toMatchObject({ state: "fuzzy", score: 0.5 });
    // A word between them is a whole word, or an edit.
    expect(as("forty thous for the venue")).toMatchObject({ state: "fuzzy", score: 0.8 });
    expect(as("rty thousand for the ven")).toMatchObject({ state: "exact", score: 1 });
  });

  it("puts a quote on whole words over parts of words, wherever its span is", () => {
    // The span is on "breathe", and "the" is said whole just after it.
    const now = lineAt("f1", 60_000, "We breathe in the venue air");
    const breathe = noteOn(now, 3, 10);
    const one = resolveSelection({ ...breathe, quoted_text: "the" }, [now]);
    expect(marked(one, [now])).toEqual([["f1", "the"]]);
    const two = lineAt("f2", 60_000, "We breathe venue air, and the venue");
    const twoWords = resolveSelection({ ...noteOn(two, 3, 16), quoted_text: "the venue" }, [two]);
    expect(twoWords.state).toBe("exact");
    expect(marked(twoWords, [two])).toEqual([["f2", "the venue"]]);
  });

  it("finds a drag from inside one speaker's line into an overlapping one as exact, on the words dragged over", () => {
    // Side-by-side lanes: "Friday" is said after the other speaker starts, so
    // the quote (time order, #616) puts "No, the" first.
    const ship: DrawnLine = { segmentId: "a", text: "Ship it on Friday", startMs: 12_000, endMs: 14_000 };
    const week: DrawnLine = { segmentId: "b", text: "No, the week after", startMs: 13_000, endMs: 15_000 };
    const selection = selectionFromPicks([
      { line: ship, from: 11, to: ship.text.length },
      { line: week, from: 0, to: 7 },
    ])!;
    expect(selection.quotedText).toBe("No, the Friday");
    const note = { start_ms: selection.startMs, end_ms: selection.endMs, quoted_text: selection.quotedText };
    for (const lines of [[ship, week], [week, ship]]) {
      const resolution = resolveSelection(note, lines);
      expect(resolution).toMatchObject({ state: "exact", score: 1 });
      expect(marked(resolution, lines)).toEqual([
        ["b", "No, the"],
        ["a", "Friday"],
      ]);
    }
    // From inside a word of the later speaker's line into the start of the earlier one's.
    const inWord = selectionFromPicks([
      { line: week, from: 10, to: week.text.length },
      { line: ship, from: 0, to: 6 },
    ])!;
    expect(inWord.quotedText).toBe("Ship i ek after");
    const resolution = resolveSelection(
      { start_ms: inWord.startMs, end_ms: inWord.endMs, quoted_text: inWord.quotedText },
      [ship, week],
    );
    expect(resolution).toMatchObject({ state: "exact", score: 1 });
    expect(marked(resolution, [ship, week])).toEqual([
      ["a", "Ship it"],
      ["b", "week after"],
    ]);
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

/**
 * A small seeded generator (mulberry32), so a failing case can be run again.
 * It keeps to 32-bit integers: a multiplier that overflows a double's 53 bits
 * repeats after some ten thousand draws, a few dozen cases.
 */
const random = (seed: number) => () => {
  seed = (seed + 0x6d2b79f5) | 0;
  let mixed = Math.imul(seed ^ (seed >>> 15), seed | 1);
  mixed ^= mixed + Math.imul(mixed ^ (mixed >>> 7), mixed | 61);
  return ((mixed ^ (mixed >>> 14)) >>> 0) / 4_294_967_296;
};

const WORDS = [
  "the", "budget", "venue", "forty", "thousand", "march", "we", "need", "council", "agreed",
  "motion", "member", "question", "yes", "no", "and", "a", "to", "of", "report", "minister",
  "noble", "lord", "safety", "water", "housing", "police", "transport", "time", "please",
];

const PUNCTUATION = ["", "", "", ",", ".", "?", "!", ";", "…"];

/** Words a pass may hear instead: none starts or ends with a word of `WORDS`, so none is part of one. */
const OTHERS = ["xylem", "kumquat", "jigsaw", "zephyr", "quiz", "vex"];

/** A quote's words, as compared. */
const quoteWords = (text: string | null | undefined) =>
  (text ?? "").split(/\s+/).map(comparableWord).filter((word) => word !== "");

/** The marked words, as compared, in order. */
const markedWords = (resolution: Resolution, lines: readonly DrawnLine[]) =>
  marked(resolution, lines).flatMap(([, text]) => quoteWords(text));

/**
 * How many of `quote`'s words `words` holds in order (longest common
 * subsequence), the quote's first and last words also as the end and start
 * of a word, and a one-word quote as any part of one, as a selection inside a
 * word quotes them.
 */
const commonInOrder = (words: readonly string[], quote: readonly string[]) => {
  const same = (word: string, index: number) =>
    word === quote[index] ||
    (quote.length === 1 && word.includes(quote[0])) ||
    (index === 0 && word.endsWith(quote[index])) ||
    (index === quote.length - 1 && word.startsWith(quote[index]));
  let row = new Array<number>(words.length + 1).fill(0);
  for (let i = 0; i < quote.length; i += 1) {
    const nextRow = [0];
    for (let j = 0; j < words.length; j += 1) {
      nextRow.push(same(words[j], i) ? row[j] + 1 : Math.max(row[j + 1], nextRow[j]));
    }
    row = nextRow;
  }
  return row[words.length];
};

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
    // Each cut also gives the index in `words` of every line's first word.
    const cut = (variant: (word: string) => string) => {
      const lines: DrawnLine[] = [];
      const starts: number[] = [];
      let start = 0;
      while (start < words.length) {
        const end = Math.min(words.length, start + 3 + Math.floor(next() * 8));
        lines.push({
          segmentId: `s${lines.length}`,
          text: words.slice(start, end).map(variant).join(" "),
          startMs: times[start],
          endMs: times[end - 1] + 300,
        });
        starts.push(start);
        start = end;
      }
      return { lines, starts };
    };
    const then = cut((word) => word);
    const now = cut((word) => {
      const cased = next() < 0.3 ? word.toUpperCase() : next() < 0.3 ? word[0].toUpperCase() + word.slice(1) : word;
      return cased + pick(PUNCTUATION);
    });
    return { then: then.lines, thenStarts: then.starts, now: now.lines, nowStarts: now.starts, pick };
  };

  /**
   * A selection of a few whole words inside one line of `then`, as B3c makes
   * it, and the indices of its words in the transcript.
   */
  const select = (then: readonly DrawnLine[], thenStarts: readonly number[], next: () => number) => {
    const lineIndex = Math.floor(next() * then.length);
    const line = then[lineIndex];
    const tokens = interpolatedTokens(line.text, line.startMs, line.endMs);
    const count = 2 + Math.floor(next() * Math.min(5, tokens.length - 1));
    const first = Math.floor(next() * (tokens.length - count + 1));
    const last = Math.min(tokens.length, first + count) - 1;
    const indices = Array.from({ length: last - first + 1 }, (_, at) => thenStarts[lineIndex] + first + at);
    return { note: noteOn(line, tokens[first].from, tokens[last].to), indices };
  };

  it("finds the quote's own words, exactly, whatever the re-cut, case and punctuation (5,000 cases)", () => {
    const next = random(488);
    for (let run = 0; run < 5_000; run += 1) {
      const { then, thenStarts, now } = transcripts(next);
      const { note } = select(then, thenStarts, next);
      const resolution = resolveSelection(note, now);
      const context = JSON.stringify({ run, note });
      expect(resolution.state, context).toBe("exact");
      expect(markedWords(resolution, now), context).toEqual(quoteWords(note.quoted_text));
    }
  });

  it("marks as many of the quote's words as its score says, with some of them heard differently (5,000 cases)", () => {
    const next = random(4_880);
    let atOrOverFloor = 0;
    for (let run = 0; run < 5_000; run += 1) {
      const { then, thenStarts, now, nowStarts, pick } = transcripts(next);
      const { note, indices } = select(then, thenStarts, next);
      const quote = quoteWords(note.quoted_text);
      // Some of the quoted words are other words now, in the same places.
      const changed = 1 + Math.floor(next() * quote.length);
      const heardAs = new Set<number>();
      while (heardAs.size < changed) heardAs.add(pick(indices));
      const replaced = now.map((line, index) => ({
        ...line,
        text: line.text
          .split(" ")
          .map((word, at) => (heardAs.has(nowStarts[index] + at) ? pick(OTHERS) : word))
          .join(" "),
      }));
      const resolution = resolveSelection(note, replaced);
      const context = JSON.stringify({ run, note, changed });
      const left = 1 - changed / quote.length;
      // The same words may be said again nearby, so it can do better, never worse.
      expect(resolution.score, context).toBeGreaterThanOrEqual(left - 1e-9);
      if (left >= ANCHOR_SCORE_FLOOR) {
        atOrOverFloor += 1;
        expect(resolution.state, context).not.toBe("unanchored");
      }
      if (resolution.state === "unanchored") continue;
      // Every word it gives a quote word for, in order, is that quote word.
      const matched = commonInOrder(markedWords(resolution, replaced), quote);
      expect(matched, context).toBeGreaterThanOrEqual(Math.round(resolution.score * quote.length));
    }
    expect(atOrOverFloor).toBeGreaterThan(1_000);
  });

  it("never marks words when the quoted ones are gone from near its span (5,000 cases)", () => {
    const next = random(3_888);
    for (let run = 0; run < 5_000; run += 1) {
      const { then, thenStarts, now } = transcripts(next);
      const { note } = select(then, thenStarts, next);
      // Near the note the new text is other words; further away it still
      // says the same, quote and all.
      const near = (line: DrawnLine) =>
        line.endMs >= note.start_ms - ANCHOR_MARGIN_MS - 1_000 &&
        line.startMs <= note.end_ms + ANCHOR_MARGIN_MS + 1_000;
      const replaced = now.map((line) =>
        near(line)
          ? {
              ...line,
              text: line.text
                .split(" ")
                .map((_, index) => OTHERS[(index + run) % OTHERS.length])
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

/**
 * Words none of which is part of another, so a part of a word picked is never
 * a whole word elsewhere: where it is, the whole word wins the tie (above).
 */
const APART = [
  "budget", "venue", "forty", "march", "council", "agreed", "motion", "member", "question",
  "report", "minister", "noble", "lord", "safety", "water", "housing", "police", "transport",
  "please", "yes", "we", "of", "it", "so",
];

describe("resolveSelection on the transcript a selection was made on", () => {
  /** Lines of `count` words, cased and punctuated at random, a word every 300-500 ms. */
  const transcript = (next: () => number, count: number, vocabulary: readonly string[] = APART): DrawnLine[] => {
    const pick = <T,>(list: readonly T[]) => list[Math.floor(next() * list.length)];
    const lines: DrawnLine[] = [];
    let at = 1_000 + Math.floor(next() * 5_000);
    for (let left = count; left > 0; ) {
      const size = Math.min(left, 3 + Math.floor(next() * 8));
      const words = Array.from({ length: size }, () => {
        const word = pick(vocabulary);
        const cased = next() < 0.2 ? word.toUpperCase() : next() < 0.2 ? word[0].toUpperCase() + word.slice(1) : word;
        return cased + pick(PUNCTUATION);
      });
      const startMs = at;
      at += size * (300 + Math.floor(next() * 200));
      lines.push({ segmentId: `s${lines.length}`, text: words.join(" "), startMs, endMs: at - 50 });
      left -= size;
    }
    return lines;
  };

  /**
   * What a drag takes: from any character of line `first` to any character of
   * line `last`, every line between whole, as `linePicksOfRange` gives it.
   */
  const drag = (lines: readonly DrawnLine[], first: number, last: number, next: () => number): LinePick[] => {
    const at = (line: DrawnLine) => Math.floor(next() * (line.text.length + 1));
    if (first === last) {
      const [from, to] = [at(lines[first]), at(lines[first])].sort((a, b) => a - b);
      return [{ line: lines[first], from, to }];
    }
    return lines.slice(first, last + 1).map((line, index, taken) => ({
      line,
      from: index === 0 ? at(line) : 0,
      to: index === taken.length - 1 ? at(line) : line.text.length,
    }));
  };

  /**
   * The words a note on `picks` is on: each word the picks touch a letter or
   * digit of, in order, up to as many as its (perhaps shortened) quote holds.
   */
  const pickedRanges = (picks: readonly LinePick[], quote: string): LineRange[] => {
    const words = picks.flatMap(({ line, from, to }) =>
      interpolatedTokens(line.text, line.startMs, line.endMs)
        .filter((token) => {
          const part = line.text.slice(Math.max(from, token.from), Math.min(to, token.to));
          return token.from < to && token.to > from && comparableWord(part) !== "";
        })
        .map((token) => ({ segmentId: line.segmentId, from: token.from, to: token.to })),
    );
    const ranges: LineRange[] = [];
    for (const word of words.slice(0, quoteWords(quote).length)) {
      const last = ranges[ranges.length - 1];
      if (last && last.segmentId === word.segmentId) last.to = word.to;
      else ranges.push({ ...word });
    }
    return ranges;
  };

  it("has words none of which is part of another", () => {
    for (const word of APART) {
      expect(APART.filter((other) => other !== word && other.includes(word)), word).toEqual([]);
    }
  });

  it("finds every selection exactly, inside words, over lines and shortened, on just the words picked (1,000 cases)", () => {
    const next = random(659);
    const seen = { partWord: 0, lines: 0, shortened: 0, tooShort: 0 };
    for (let run = 0; run < 1_000; run += 1) {
      const kind = run % 3;
      // A shortened quote needs a selection of over 2,000 characters.
      const lines = transcript(next, kind === 2 ? 420 : 40 + Math.floor(next() * 40));
      const first = kind === 2 ? Math.floor(next() * 3) : Math.floor(next() * lines.length);
      const last =
        kind === 0 ? first : kind === 1 ? Math.min(lines.length - 1, first + 1 + Math.floor(next() * 3)) : lines.length - 1 - Math.floor(next() * 3);
      const picks = drag(lines, first, last, next);
      const selection = selectionFromPicks(picks);
      if (!selection || quoteWords(selection.quotedText).length === 0) continue;
      const note = { start_ms: selection.startMs, end_ms: selection.endMs, quoted_text: selection.quotedText };
      const resolution = resolveSelection(note, lines);
      const context = JSON.stringify({ run, picks: picks.map(({ line, from, to }) => [line.segmentId, from, to]) });
      // A part of one word shorter than PART_WORD_MIN_CHARS is found nowhere:
      // none of APART holds another, so it is no whole word.
      const words = quoteWords(selection.quotedText);
      if (words.length === 1 && words[0].length < PART_WORD_MIN_CHARS && !APART.includes(words[0])) {
        expect(resolution, context).toMatchObject({ state: "unanchored", ranges: [] });
        seen.tooShort += 1;
        continue;
      }
      expect(resolution.state, context).toBe("exact");
      const picked = pickedRanges(picks, selection.quotedText);
      // A selection from the punctuation after a word takes that word's time,
      // and a one-word quote can't tell it from the same word after it
      // ("Noble? noble" from the "?"): either is right.
      const [mark] = resolution.ranges;
      const touchedTwin =
        words.length === 1 &&
        resolution.ranges.length === 1 &&
        picks.some(({ line, from, to }) => line.segmentId === mark.segmentId && mark.from < to && mark.to > from) &&
        markedWords(resolution, lines)[0].includes(words[0]);
      if (!touchedTwin) expect(resolution.ranges, context).toEqual(picked);
      const inWord = (line: DrawnLine, at: number) => /\S\S/.test(line.text.slice(at - 1, at + 1)) && at > 0;
      if (picks.some(({ line, from, to }) => inWord(line, from) || inWord(line, to))) seen.partWord += 1;
      if (picks.length > 1) seen.lines += 1;
      if (selection.quotedText.endsWith("…")) seen.shortened += 1;
    }
    expect(seen.partWord).toBeGreaterThan(500);
    expect(seen.lines).toBeGreaterThan(500);
    expect(seen.shortened).toBeGreaterThan(300);
    expect(seen.tooShort).toBeGreaterThan(0);
    // Each shortened case resolves a 2,000-character quote: under a second
    // here, so CI's slower runners need more than the default 5 s.
  }, 20_000);

  /**
   * Three hundred six-letter words: none is part of another, and few repeat
   * near each other, so a word a drag picks at the start or end of a line is
   * seldom said on the line beside it too, where it would be found as well.
   */
  const MANY = (() => {
    const next = random(42);
    const words = new Set<string>();
    while (words.size < 300) {
      words.add(Array.from({ length: 6 }, () => String.fromCharCode(97 + Math.floor(next() * 26))).join(""));
    }
    return [...words];
  })();

  it("finds every drag over two speakers' side-by-side lanes exactly, on just the words picked (2,000 cases)", () => {
    const next = random(616);
    const seen = { drags: 0, onThePicks: 0, outOfPageOrder: 0 };
    for (let run = 0; run < 2_000; run += 1) {
      // Two speakers talking over each other. The page draws each run of lines
      // that overlap in time as a group, one speaker's lane and then the other's.
      const lines = [0, 1]
        .flatMap((speaker) =>
          transcript(next, 10 + Math.floor(next() * 20), MANY).map((line, index) => ({
            ...line,
            segmentId: `${speaker}-${index}`,
          })),
        )
        .sort((left, right) => left.startMs - right.startMs);
      const groups: DrawnLine[][] = [];
      let groupEndMs = -Infinity;
      for (const line of lines) {
        if (line.startMs < groupEndMs) {
          groups[groups.length - 1].push(line);
          groupEndMs = Math.max(groupEndMs, line.endMs);
        } else {
          groups.push([line]);
          groupEndMs = line.endMs;
        }
      }
      const page = groups.flatMap((group) =>
        [0, 1].flatMap((speaker) => group.filter((line) => line.segmentId.startsWith(`${speaker}-`))),
      );
      const first = Math.floor(next() * page.length);
      const picks = drag(page, first, Math.min(page.length - 1, first + 1 + Math.floor(next() * 6)), next);
      const selection = selectionFromPicks(picks);
      if (!selection || quoteWords(selection.quotedText).length < 2) continue;
      const note = { start_ms: selection.startMs, end_ms: selection.endMs, quoted_text: selection.quotedText };
      const resolution = resolveSelection(note, page);
      const context = JSON.stringify({ run, picks: picks.map(({ line, from, to }) => [line.segmentId, from, to]) });
      expect(resolution.state, context).toBe("exact");
      // The quote's order (#616), which is the order of the words marked.
      const inTimeOrder = pickSpansInTimeOrder(picks).map(({ pick }) => pick);
      seen.drags += 1;
      if (JSON.stringify(resolution.ranges) === JSON.stringify(pickedRanges(inTimeOrder, selection.quotedText))) {
        seen.onThePicks += 1;
      }
      if (inTimeOrder.some((pick, at) => pick !== picks[at])) seen.outOfPageOrder += 1;
    }
    expect(seen.outOfPageOrder).toBeGreaterThan(500);
    // A letter or two a drag cuts at the edge of a line may also start or end
    // a word of a line beside it, where it is as exact: elsewhere, the words picked.
    expect(seen.onThePicks / seen.drags).toBeGreaterThan(0.97);
  });
});
