import { describe, expect, it, vi } from "vitest";

import { withProcessTimeZone } from "@/test/processTimeZone";
import {
  formatTimeZoneLocale,
  localDayRangeToUtc,
  setCachedUserTimeZone,
  subscribeUserTimeZone,
} from "./timezone";

describe("localDayRangeToUtc", () => {
  it("returns the UTC instants spanning a UTC local day", () => {
    const { startISO, endISO } = localDayRangeToUtc("2026-04-10", "UTC");

    expect(startISO).toBe("2026-04-10T00:00:00.000Z");
    // End is the next local midnight minus 1 ms.
    expect(endISO).toBe("2026-04-10T23:59:59.999Z");
  });

  it("shifts the UTC range for a non-UTC timezone", () => {
    // Europe/Madrid is UTC+2 in summer, so the local day starts 2h earlier.
    const { startISO, endISO } = localDayRangeToUtc(
      "2026-06-01",
      "Europe/Madrid",
    );

    expect(startISO).toBe("2026-05-31T22:00:00.000Z");
    expect(endISO).toBe("2026-06-01T21:59:59.999Z");
  });

  it("keeps the end 1 ms before the next local midnight", () => {
    const { endISO } = localDayRangeToUtc("2026-04-10", "UTC");
    const next = localDayRangeToUtc("2026-04-11", "UTC");

    expect(new Date(next.startISO).getTime() - new Date(endISO).getTime()).toBe(
      1,
    );
  });

  it("handles a spring-forward DST day", () => {
    // Europe/Madrid springs forward on 2026-03-29 (UTC+1 -> UTC+2). Local
    // midnight on that day is still UTC+1, so the day starts at 23:00 UTC the
    // previous day; the next local midnight is UTC+2, so the day is 23h long.
    const { startISO, endISO } = localDayRangeToUtc(
      "2026-03-29",
      "Europe/Madrid",
    );

    expect(startISO).toBe("2026-03-28T23:00:00.000Z");
    expect(endISO).toBe("2026-03-29T21:59:59.999Z");
  });
});

describe("formatTimeZoneLocale", () => {
  // The browser is in New York; the zone asked for is Tokyo, a day ahead here.
  withProcessTimeZone("America/New_York");
  const instant = new Date("2026-10-27T02:30:00Z");

  it("formats each part in the zone it is given, not the browser's", () => {
    expect(formatTimeZoneLocale(instant, "Asia/Tokyo", "date")).toBe(
      instant.toLocaleDateString(undefined, { timeZone: "Asia/Tokyo" }),
    );
    expect(formatTimeZoneLocale(instant, "Asia/Tokyo", "time")).toBe(
      instant.toLocaleTimeString(undefined, { timeZone: "Asia/Tokyo" }),
    );
    expect(formatTimeZoneLocale(instant, "Asia/Tokyo")).toBe(
      instant.toLocaleString(undefined, { timeZone: "Asia/Tokyo" }),
    );
    expect(formatTimeZoneLocale(instant, "Asia/Tokyo", "date")).not.toBe(
      instant.toLocaleDateString(),
    );
  });

  it("keeps the caller's options and locale", () => {
    expect(
      formatTimeZoneLocale(
        instant,
        "Asia/Tokyo",
        "date",
        { day: "numeric", month: "short", year: "numeric" },
        "en-GB",
      ),
    ).toBe("27 Oct 2026");
    expect(
      formatTimeZoneLocale(instant.getTime(), "Asia/Tokyo", "time", {
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
      }, "en-GB"),
    ).toBe("11:30");
  });

  it("falls back to UTC for a zone it does not know", () => {
    expect(formatTimeZoneLocale(instant, "Not/AZone", "time", {
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    }, "en-GB")).toBe("02:30");
  });
});

describe("subscribeUserTimeZone", () => {
  it("tells a subscriber when the cached zone changes, and only then", () => {
    setCachedUserTimeZone("Europe/London");
    const listener = vi.fn();
    const unsubscribe = subscribeUserTimeZone(listener);

    setCachedUserTimeZone("Europe/London");
    expect(listener).not.toHaveBeenCalled();

    setCachedUserTimeZone("Asia/Tokyo");
    expect(listener).toHaveBeenCalledTimes(1);

    unsubscribe();
    setCachedUserTimeZone("Europe/Paris");
    expect(listener).toHaveBeenCalledTimes(1);
  });
});
