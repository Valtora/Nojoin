import { describe, expect, it } from "vitest";

import { mergeAutosaveStates } from "./settingsState";

// Search-state tests moved to settingsRegistry.test.ts: search now resolves to
// an individual setting rather than picking a tab, so the scoring it used to
// assert against no longer exists.

describe("mergeAutosaveStates", () => {
  it("returns saved when no autosave states are present", () => {
    expect(mergeAutosaveStates()).toEqual({ status: "saved" });
  });

  it("returns the highest-priority autosave state", () => {
    expect(
      mergeAutosaveStates(
        { status: "pending", message: "Pending" },
        { status: "error", message: "Error" },
        { status: "saved", message: "Saved" },
      ),
    ).toEqual({ status: "error", message: "Error" });

    expect(
      mergeAutosaveStates(
        { status: "saving", message: "Saving" },
        { status: "blocked", message: "Blocked" },
      ),
    ).toEqual({ status: "blocked", message: "Blocked" });
  });

  it("ignores absent states rather than treating them as saved", () => {
    // The account autosave slot is null until account settings mount, which
    // must not report success over a save that is still in flight.
    expect(
      mergeAutosaveStates(null, { status: "saving", message: "Saving" }, undefined),
    ).toEqual({ status: "saving", message: "Saving" });
  });
});
