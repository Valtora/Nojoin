import { describe, expect, it } from "vitest";

import { canEditRecording, ownerLabel, ownsRecording } from "./access";

describe("sharing access helpers", () => {
  it("treats a recording without an access field as the caller's own", () => {
    expect(ownsRecording({})).toBe(true);
    expect(canEditRecording({})).toBe(true);
  });

  it("lets editors edit but not own", () => {
    expect(ownsRecording({ access: "edit" })).toBe(false);
    expect(canEditRecording({ access: "edit" })).toBe(true);
    expect(canEditRecording({ access: "view" })).toBe(false);
  });

  it("names the owner by display name, then username", () => {
    expect(ownerLabel({ owner: { username: "job", display_name: "Job" } })).toBe("Job");
    expect(ownerLabel({ owner: { username: "job" } })).toBe("job");
    expect(ownerLabel({ owner: null })).toBe("someone");
  });
});
