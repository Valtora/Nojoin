import { formatInTimeZone, fromZonedTime, toZonedTime } from "date-fns-tz";

import { getSettings } from "@/lib/api";

export const DEFAULT_TIME_ZONE = "UTC";

const FALLBACK_TIME_ZONES = [
  "UTC",
  "Europe/London",
  "Europe/Dublin",
  "Europe/Paris",
  "Europe/Berlin",
  "Europe/Madrid",
  "Europe/Rome",
  "Europe/Amsterdam",
  "Europe/Zurich",
  "Europe/Stockholm",
  "America/New_York",
  "America/Chicago",
  "America/Denver",
  "America/Los_Angeles",
  "America/Toronto",
  "America/Vancouver",
  "America/Sao_Paulo",
  "Asia/Dubai",
  "Asia/Kolkata",
  "Asia/Singapore",
  "Asia/Tokyo",
  "Asia/Hong_Kong",
  "Asia/Seoul",
  "Australia/Sydney",
  "Pacific/Auckland",
];

let cachedUserTimeZone: string | null = null;
let pendingUserTimeZone: Promise<string> | null = null;

export function isValidTimeZone(value: string | null | undefined): value is string {
  if (!value) {
    return false;
  }

  try {
    new Intl.DateTimeFormat("en-US", { timeZone: value }).format(new Date());
    return true;
  } catch {
    return false;
  }
}

export function normaliseTimeZone(value: string | null | undefined): string | null {
  if (!value) {
    return null;
  }

  const cleaned = value.trim();
  if (!cleaned) {
    return null;
  }

  if (cleaned.toUpperCase() === DEFAULT_TIME_ZONE) {
    return DEFAULT_TIME_ZONE;
  }

  return isValidTimeZone(cleaned) ? cleaned : null;
}

export function resolveTimeZone(...candidates: Array<string | null | undefined>): string {
  for (const candidate of candidates) {
    const normalised = normaliseTimeZone(candidate);
    if (normalised) {
      return normalised;
    }
  }

  return DEFAULT_TIME_ZONE;
}

export function getBrowserTimeZone(): string {
  if (typeof window === "undefined") {
    return DEFAULT_TIME_ZONE;
  }

  const detected = Intl.DateTimeFormat().resolvedOptions().timeZone;
  return resolveTimeZone(detected, DEFAULT_TIME_ZONE);
}

export function getSupportedTimeZones(): string[] {
  const timeZones =
    typeof Intl.supportedValuesOf === "function"
      ? Intl.supportedValuesOf("timeZone")
      : FALLBACK_TIME_ZONES;

  return Array.from(new Set([DEFAULT_TIME_ZONE, ...timeZones])).sort(
    (left, right) => {
      if (left === DEFAULT_TIME_ZONE) {
        return -1;
      }

      if (right === DEFAULT_TIME_ZONE) {
        return 1;
      }

      return left.localeCompare(right);
    },
  );
}

export function setCachedUserTimeZone(value: string | null | undefined): string {
  const resolved = resolveTimeZone(value, DEFAULT_TIME_ZONE);
  cachedUserTimeZone = resolved;
  return resolved;
}

export async function getUserTimeZone(): Promise<string> {
  if (cachedUserTimeZone) {
    return cachedUserTimeZone;
  }

  if (pendingUserTimeZone) {
    return pendingUserTimeZone;
  }

  pendingUserTimeZone = getSettings()
    .then((settings) => setCachedUserTimeZone(settings.timezone))
    .catch(() => setCachedUserTimeZone(getBrowserTimeZone()))
    .finally(() => {
      pendingUserTimeZone = null;
    });

  return pendingUserTimeZone;
}

export function formatTimeZoneDate(
  value: Date | string | number,
  timeZone: string,
  pattern: string,
): string {
  return formatInTimeZone(value, resolveTimeZone(timeZone), pattern);
}

export function toTimeZoneDate(
  value: Date | string | number,
  timeZone: string,
): Date {
  return toZonedTime(value, resolveTimeZone(timeZone));
}

export function fromTimeZoneDate(value: Date, timeZone: string): Date {
  return fromZonedTime(value, resolveTimeZone(timeZone));
}

/**
 * Convert a local calendar day (YYYY-MM-DD) into a UTC instant range.
 *
 * `startISO` is the UTC instant of `localDate` at 00:00:00.000 in `timeZone`.
 * `endISO` is the UTC instant of the *next* local day at 00:00:00.000 in
 * `timeZone`, minus 1 ms — so it pairs with a `<=` end-date filter and stays
 * inclusive-start / exclusive-next-day.
 */
export function localDayRangeToUtc(
  localDate: string,
  timeZone: string,
): { startISO: string; endISO: string } {
  const [year, month, day] = localDate.split("-").map((part) => Number(part));

  const startLocal = new Date(year, month - 1, day, 0, 0, 0, 0);
  const nextDayLocal = new Date(year, month - 1, day + 1, 0, 0, 0, 0);

  const startUtc = fromTimeZoneDate(startLocal, timeZone);
  const nextDayUtc = fromTimeZoneDate(nextDayLocal, timeZone);
  const endUtc = new Date(nextDayUtc.getTime() - 1);

  return { startISO: startUtc.toISOString(), endISO: endUtc.toISOString() };
}