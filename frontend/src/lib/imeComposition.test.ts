// @vitest-environment node
import { describe, expect, it } from "vitest";

import { isImeComposing } from "./imeComposition";

const press = (isComposing: boolean, keyCode: number) => ({ isComposing, keyCode });

describe("isImeComposing", () => {
  it("is true for a press flagged as part of a composition", () => {
    expect(isImeComposing(press(true, 13))).toBe(true);
  });

  it("is true for the keyCode 229 Safari reports after it ends a composition", () => {
    expect(isImeComposing(press(false, 229))).toBe(true);
  });

  it("is false for an ordinary press", () => {
    expect(isImeComposing(press(false, 13))).toBe(false);
    expect(isImeComposing(press(false, 27))).toBe(false);
  });

  it("reads a React event's native event, where isComposing lives", () => {
    expect(isImeComposing({ nativeEvent: press(true, 40) })).toBe(true);
    expect(isImeComposing({ nativeEvent: press(false, 229) })).toBe(true);
    expect(isImeComposing({ nativeEvent: press(false, 40) })).toBe(false);
  });
});
