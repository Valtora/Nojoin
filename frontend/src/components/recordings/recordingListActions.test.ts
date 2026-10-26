// @vitest-environment node

import { describe, expect, it } from "vitest";

import {
  LIST_ACTIONS,
  RESULT_TEXT,
  VIEW_ACTIONS,
  describeResult,
  movedBackText,
  permanentDeleteConfirmation,
  tagResultText,
} from "./recordingListActions";

const result = (count: number, skipped: number) => ({
  count,
  skipped,
  recording_ids: Array.from({ length: count }, (_, index) => `rec-${index}`),
});

describe("the recordings list action table", () => {
  it("offers one set of actions per view", () => {
    expect(VIEW_ACTIONS).toEqual({
      recordings: ["tag", "archive", "softDelete"],
      archived: ["tag", "unarchive", "softDelete"],
      deleted: ["restore", "permanentDelete"],
    });
  });

  it("gives every action its own label and icon", () => {
    const actions = Object.values(LIST_ACTIONS);

    expect(new Set(actions.map(({ label }) => label)).size).toBe(actions.length);
    expect(new Set(actions.map(({ icon }) => icon)).size).toBe(actions.length);
  });

  it("confirms only permanent delete", () => {
    expect(
      Object.entries(LIST_ACTIONS)
        .filter(([, action]) => action.destructive)
        .map(([id]) => id),
    ).toEqual(["permanentDelete"]);
    expect(permanentDeleteConfirmation(1).title).toBe(
      "Delete this recording permanently?",
    );
    expect(permanentDeleteConfirmation(3).title).toBe(
      "Delete 3 recordings permanently?",
    );
  });
});

describe("describeResult", () => {
  it("counts what the server changed, in the singular for one", () => {
    expect(describeResult(RESULT_TEXT.restore, result(1, 0))).toEqual({
      type: "success",
      message: "Restored 1 recording.",
    });
    expect(describeResult(RESULT_TEXT.softDelete, result(4, 0)).message).toBe(
      "Moved 4 recordings to the bin.",
    );
  });

  it("says how many of those asked for it changed", () => {
    expect(describeResult(RESULT_TEXT.archive, result(2, 1)).message).toBe(
      "Archived 2 of 3 recordings.",
    );
    expect(describeResult(RESULT_TEXT.permanentDelete, result(1, 2)).message).toBe(
      "Deleted 1 of 3 recordings permanently. 2 were no longer in the bin, so they were kept.",
    );
    expect(describeResult(tagResultText("Board"), result(2, 1)).message).toBe(
      'Added "Board" to 2 of 3 recordings. The other already had it.',
    );
  });

  it("warns when nothing changed", () => {
    expect(describeResult(RESULT_TEXT.permanentDelete, result(0, 1))).toEqual({
      type: "warning",
      message: "Nothing was deleted: the recording was no longer in the bin.",
    });
    expect(describeResult(tagResultText("Board"), result(0, 2)).message).toBe(
      'The recordings already had "Board".',
    );
  });

  it("says where an undo put recordings back", () => {
    expect(movedBackText(1, "the bin")).toBe("Moved 1 recording back to the bin.");
    expect(movedBackText(2, "Archived")).toBe("Moved 2 recordings back to Archived.");
  });
});
