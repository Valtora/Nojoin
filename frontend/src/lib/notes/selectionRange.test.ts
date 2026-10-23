import { afterEach, describe, expect, it } from "vitest";

import { noteLength } from "./headNoteSync";
import { NOTE_QUOTE_MAX_CHARS } from "./noteOutbox";
import {
  interpolatedTokens,
  linePicksOfRange,
  selectionFromPicks,
  type DrawnLine,
} from "./selectionRange";

// "Forty thousand for the venue": 28 characters over 12.0 s to 15.5 s, so
// 125 ms a character.
const VENUE: DrawnLine = { segmentId: "u1", text: "Forty thousand for the venue", startMs: 12_000, endMs: 15_500 };

/** The lines as the views draw them: one `p[data-segment-id]` each, with its span. */
const drawLines = (html: string) => {
  document.body.innerHTML = `<div id="root">${html}</div>`;
  return document.getElementById("root")!;
};
const line = ({ segmentId, text, startMs, endMs }: DrawnLine, inner = text) =>
  `<p data-segment-id="${segmentId}" data-start-ms="${startMs}" data-end-ms="${endMs}">${inner}</p>`;

/** A range from `start` characters into the first line given to `end` into the second. */
const rangeOver = (from: [Element, number], to: [Element, number]) => {
  const at = ([paragraph, offset]: [Element, number]): [Node, number] => {
    const walker = document.createTreeWalker(paragraph, NodeFilter.SHOW_TEXT);
    let left = offset;
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      const length = node.textContent!.length;
      if (left <= length) return [node, left];
      left -= length;
    }
    throw new Error("offset past the line");
  };
  const range = document.createRange();
  range.setStart(...at(from));
  range.setEnd(...at(to));
  return range;
};

const select = (root: Element, from: [string, number], to: [string, number]) => {
  const p = (id: string) => root.querySelector(`p[data-segment-id="${id}"]`)!;
  return selectionFromPicks(linePicksOfRange(rangeOver([p(from[0]), from[1]], [p(to[0]), to[1]]), root));
};

afterEach(() => {
  document.body.innerHTML = "";
});

describe("interpolatedTokens", () => {
  it("times each word by its characters, linearly across the line's span", () => {
    expect(interpolatedTokens(VENUE.text, VENUE.startMs, VENUE.endMs).slice(0, 2)).toEqual([
      { text: "Forty", from: 0, to: 5, startMs: 12_000, endMs: 12_625 },
      { text: "thousand", from: 6, to: 14, startMs: 12_750, endMs: 13_750 },
    ]);
  });

  it("gives every word the line's start when the line has no span", () => {
    expect(interpolatedTokens("a b", 5_000, 5_000).map((token) => [token.startMs, token.endMs])).toEqual([
      [5_000, 5_000],
      [5_000, 5_000],
    ]);
  });
});

describe("a selection in one line", () => {
  it("runs from its first word to its last, quoting the text as selected", () => {
    const root = drawLines(line(VENUE));
    expect(select(root, ["u1", 6], ["u1", 18])).toEqual({
      startMs: 12_750,
      endMs: 14_250,
      quotedText: "thousand for",
    });
  });

  it("takes the whole word's time for part of a word, and quotes the part", () => {
    const root = drawLines(line(VENUE));
    expect(select(root, ["u1", 8], ["u1", 14])).toEqual({
      startMs: 12_750,
      endMs: 13_750,
      quotedText: "ousand",
    });
  });

  it("reads the line's text through the marks a search draws in it", () => {
    const root = drawLines(line(VENUE, "Forty <mark>thou</mark>sand for the venue"));
    expect(select(root, ["u1", 6], ["u1", 14])).toMatchObject({ quotedText: "thousand" });
  });

  it("makes no note of spaces alone", () => {
    const root = drawLines(line(VENUE));
    expect(select(root, ["u1", 5], ["u1", 6])).toBeNull();
  });
});

describe("a selection across lines", () => {
  const NEXT: DrawnLine = { segmentId: "u2", text: "Catering is extra", startMs: 16_000, endMs: 18_000 };

  it("runs from the first line's first word taken to the last line's last, quoting both", () => {
    const root = drawLines(line(VENUE) + line(NEXT));
    expect(select(root, ["u1", 19], ["u2", 8])).toEqual({
      startMs: 14_375,
      endMs: 16_941,
      quotedText: "the venue Catering",
    });
  });

  it("joins the lines with one space, whatever spaces they start or end with", () => {
    const spaced: DrawnLine = { ...NEXT, text: "  Catering is extra" };
    const root = drawLines(line(VENUE) + line(spaced));
    expect(select(root, ["u1", 23], ["u2", 10])).toMatchObject({ quotedText: "venue Catering" });
  });

  it("quotes lanes of overlapping speech in time order, not page order", () => {
    // Side-by-side lanes: the later speaker's lane comes first on the page.
    const later: DrawnLine = { segmentId: "b", text: "No, the week after", startMs: 13_000, endMs: 15_000 };
    const earlier: DrawnLine = { segmentId: "a", text: "Ship it on Friday", startMs: 12_000, endMs: 14_000 };
    const root = drawLines(`<div>${line(later)}</div><div>${line(earlier)}</div>`);
    expect(select(root, ["b", 0], ["a", 17])).toEqual({
      startMs: 12_000,
      endMs: 15_000,
      quotedText: "Ship it on Friday No, the week after",
    });
  });

  it("cuts a quote longer than a note keeps", () => {
    const long: DrawnLine = { segmentId: "u3", text: "word ".repeat(500).trim(), startMs: 0, endMs: 600_000 };
    const root = drawLines(line(long) + line({ ...long, segmentId: "u4", startMs: 600_000, endMs: 1_200_000 }));
    const selection = select(root, ["u3", 0], ["u4", long.text.length])!;
    expect(noteLength(selection.quotedText)).toBe(NOTE_QUOTE_MAX_CHARS);
    expect(selection.quotedText.endsWith("…")).toBe(true);
    expect([selection.startMs, selection.endMs]).toEqual([0, 1_200_000]);
  });

  it("leaves out text that isn't a line's, such as a speaker's name", () => {
    const root = drawLines(`<span id="name">Priya</span>${line(VENUE)}`);
    const range = document.createRange();
    range.setStart(root.querySelector("#name")!.firstChild!, 0);
    range.setEnd(root.querySelector("p")!.firstChild!, 5);
    expect(selectionFromPicks(linePicksOfRange(range, root))).toEqual({
      startMs: 12_000,
      endMs: 12_625,
      quotedText: "Forty",
    });
  });
});
