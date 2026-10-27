import { afterEach, beforeEach, expect } from "vitest";

/** Runs each test of the enclosing describe with the process in `timeZone`. */
export function withProcessTimeZone(timeZone: string): void {
  const originalTimeZone = process.env.TZ;

  beforeEach(() => {
    process.env.TZ = timeZone;
    // A worker thread ignores a run-time TZ change, and would test UTC instead.
    expect(new Date(Date.UTC(2026, 9, 27)).getTimezoneOffset()).not.toBe(0);
  });

  afterEach(() => {
    if (originalTimeZone === undefined) delete process.env.TZ;
    else process.env.TZ = originalTimeZone;
  });
}
