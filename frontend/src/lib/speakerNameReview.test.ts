import { describe, expect, it } from "vitest";

import { describeSuggestedNameSource } from "./speakerNameReview";

describe("describeSuggestedNameSource", () => {
  it("names each source of a suggestion", () => {
    expect(describeSuggestedNameSource({ name_source: "voiceprint" })).toBe("Recognised by voice");
    expect(describeSuggestedNameSource({ name_source: "llm" })).toBe("Suggested by AI");
    expect(describeSuggestedNameSource({ name_source: "heuristic" })).toBe("Introduced themselves");
    expect(describeSuggestedNameSource({ name_source: "user" })).toBe("You gave this name earlier");
  });

  it("has nothing to say for a confirmed source or none", () => {
    expect(describeSuggestedNameSource({ name_source: "auto" })).toBeNull();
    expect(describeSuggestedNameSource({ name_source: null })).toBeNull();
  });
});
