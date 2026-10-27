import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { parseServerTimestamp } from "./serverTimestamp";

const originalTimeZone = process.env.TZ;

describe.each(["America/New_York", "Asia/Tokyo"])("parseServerTimestamp in %s", (timeZone) => {
  beforeEach(() => {
    process.env.TZ = timeZone;
  });

  afterEach(() => {
    if (originalTimeZone === undefined) delete process.env.TZ;
    else process.env.TZ = originalTimeZone;
  });

  it.each([
    "2026-10-27T16:08:13.123",
    "2026-10-27 16:08:13.123456",
    "2026-10-27T16:08:13.123Z",
    "2026-10-27T16:08:13.123z",
    "2026-10-27T12:08:13.123-04:00",
    "2026-10-28T01:08:13.123+0900",
    " 2026-10-27T16:08:13.123 ",
  ])("resolves %s to the same UTC instant", (value) => {
    expect(parseServerTimestamp(value)?.getTime()).toBe(1793117293123);
  });

  it("accepts a zone-less timestamp without seconds", () => {
    expect(parseServerTimestamp("2026-10-27T16:08")?.toISOString()).toBe("2026-10-27T16:08:00.000Z");
  });

  it("does not normalise a UTC instant through a local daylight-saving gap", () => {
    expect(parseServerTimestamp("2026-03-08T02:30:00")?.toISOString()).toBe("2026-03-08T02:30:00.000Z");
  });

  it("preserves date-only parsing", () => {
    expect(parseServerTimestamp("2026-10-27")?.toISOString()).toBe("2026-10-27T00:00:00.000Z");
  });

  it.each([null, undefined, "", "  ", "invalid", "2026-13-27T16:08:13", "2026-10-27T25:08:13Z"])(
    "rejects absent or invalid input %s", (value) => {
      expect(parseServerTimestamp(value)).toBeNull();
    },
  );
});
