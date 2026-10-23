import { describe, expect, it } from "vitest";

import { lineActionsFor, type LineActionContext } from "./actions";

const ids = (context: LineActionContext) => lineActionsFor(context).map((action) => action.id);

const FINISHED: LineActionContext = {
  view: "finished",
  canNote: true,
  canEdit: true,
  provisional: false,
  canPlay: true,
};

describe("lineActionsFor", () => {
  it("offers a finished line a note, its edits and playback, in that order", () => {
    expect(ids(FINISHED)).toEqual(["note", "editText", "changeSpeaker", "play"]);
  });

  it("offers a live line only a note, whatever else the context says", () => {
    expect(ids({ ...FINISHED, view: "live" })).toEqual(["note"]);
  });

  it("leaves out the edits on a line that can't be changed, and playback where it is off", () => {
    expect(ids({ ...FINISHED, canEdit: false })).toEqual(["note", "play"]);
    expect(ids({ ...FINISHED, canPlay: false })).toEqual(["note", "editText", "changeSpeaker"]);
  });

  it("offers no text edit on a line still live, which the final pass replaces", () => {
    expect(ids({ ...FINISHED, provisional: true })).toEqual(["note", "changeSpeaker", "play"]);
  });

  it("leaves out the note where none can be added", () => {
    expect(ids({ ...FINISHED, canNote: false })).toEqual(["editText", "changeSpeaker", "play"]);
    expect(ids({ ...FINISHED, view: "live", canNote: false })).toEqual([]);
  });
});
