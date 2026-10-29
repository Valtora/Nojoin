import { describe, expect, it } from "vitest";

import { newNoteId, noteLength, stripNote } from "./headNoteSync";

describe("stripNote and noteLength", () => {
  it("trim and count a note as the API's Python does", () => {
    expect(stripNote("\u001c\u0085 note \u3000")).toBe("note");
    expect(stripNote("\ufeffnote")).toBe("\ufeffnote");
    expect(noteLength("\u{1F600}a")).toBe(2);
  });
});

describe("newNoteId", () => {
  it("is a version 4 UUID", () => {
    const ids = new Set(Array.from({ length: 50 }, newNoteId));
    expect(ids.size).toBe(50);
    for (const id of ids) {
      expect(id).toMatch(
        /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
      );
    }
  });
});
