const ZONELESS_DATETIME = /^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?$/i;

/**
 * Parse an API timestamp. The server's zone-less date-times represent UTC;
 * explicit zones and date-only values retain their standard Date semantics.
 * Returns null for absent or unparseable values. Do not use for local form input.
 */
export function parseServerTimestamp(value: string | null | undefined): Date | null {
  if (!value?.trim()) return null;
  const timestamp = value.trim();
  const date = new Date(ZONELESS_DATETIME.test(timestamp) ? `${timestamp}Z` : timestamp);
  return Number.isNaN(date.getTime()) ? null : date;
}
