import { describe, expect, it } from "vitest";

import {
  getPreferredSettingsSectionForSearch,
  getSettingsSectionMatchScores,
  mergeAutosaveStates,
} from "./settingsState";

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
});

describe("settings search state", () => {
  it("prefers the best matching section for admin-only queries", () => {
    const matchScores = getSettingsSectionMatchScores({
      searchQuery: "backup",
      isAdmin: true,
    });

    expect(matchScores?.administration).toBe(0);
    expect(
      getPreferredSettingsSectionForSearch({
        activeSectionId: "personal",
        matchScores,
      }),
    ).toBe("administration");
  });

  it("does not route non-admin users into hidden administration content", () => {
    const matchScores = getSettingsSectionMatchScores({
      searchQuery: "backup",
      isAdmin: false,
    });

    expect(matchScores?.administration).toBe(1);
    expect(
      getPreferredSettingsSectionForSearch({
        activeSectionId: "personal",
        matchScores,
      }),
    ).not.toBe("administration");
  });

  it("switches to an exact-match section when the current section is weaker", () => {
    const matchScores = getSettingsSectionMatchScores({
      searchQuery: "help",
      isAdmin: true,
    });

    expect(
      getPreferredSettingsSectionForSearch({
        activeSectionId: "companion",
        matchScores,
      }),
    ).toBe("help");
  });
});