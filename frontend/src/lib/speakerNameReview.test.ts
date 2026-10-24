import { describe, expect, it } from "vitest";

import { describeSuggestedNameSource } from "./speakerNameReview";

describe("describeSuggestedNameSource", () => {
  it("gives a voiceprint match's score as a whole percent", () => {
    expect(
      describeSuggestedNameSource({ name_source: "voiceprint", identity_confidence: 0.816 }),
    ).toBe("Recognised by voice (82%)");
  });

  it("leaves the score out when the match has none", () => {
    expect(
      describeSuggestedNameSource({ name_source: "voiceprint", identity_confidence: null }),
    ).toBe("Recognised by voice");
  });

  it("names the other sources", () => {
    expect(describeSuggestedNameSource({ name_source: "llm" })).toBe("Suggested by AI");
    expect(describeSuggestedNameSource({ name_source: "heuristic" })).toBe("Introduced themselves");
    expect(describeSuggestedNameSource({ name_source: "user" })).toBe(
      "Your name for part of this speech",
    );
  });

  it("has nothing to say for a confirmed source or none", () => {
    expect(describeSuggestedNameSource({ name_source: "auto" })).toBeNull();
    expect(describeSuggestedNameSource({ name_source: null })).toBeNull();
  });
});
