import { describe, expect, it } from "vitest";

import {
  getVisibleSettingsSections,
  resolveLegacySettingsSectionId,
} from "./settingsMetadata";

describe("settingsMetadata", () => {
  it("keeps only personal settings visible during forced password changes", () => {
    const sections = getVisibleSettingsSections({
      isAdmin: true,
      forcePasswordChange: true,
    });

    expect(sections.map((section) => section.id)).toEqual(["personal"]);
  });

  it("hides administration from non-admin users", () => {
    const sections = getVisibleSettingsSections({
      isAdmin: false,
      forcePasswordChange: false,
    });

    expect(sections.map((section) => section.id)).toEqual([
      "personal",
      "ai",
      "companion",
      "updates",
      "help",
    ]);
  });

  it("maps legacy tab ids onto the unified section ids", () => {
    expect(resolveLegacySettingsSectionId("general")).toBe("personal");
    expect(resolveLegacySettingsSectionId("account")).toBe("personal");
    expect(resolveLegacySettingsSectionId("audio")).toBe("companion");
    expect(resolveLegacySettingsSectionId("admin")).toBe("administration");
    expect(resolveLegacySettingsSectionId("ai")).toBe("ai");
    expect(resolveLegacySettingsSectionId("unknown")).toBeNull();
  });
});