/** Durations for reading, not for precision. */
export const formatDuration = (ms: number): string => {
  const totalSeconds = Math.round(ms / 1000);
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  if (hours > 0) return `${hours}h ${minutes}m`;
  if (minutes > 0) return `${minutes}m ${seconds}s`;
  return `${seconds}s`;
};

/** A clock position, for anything the user can seek to. */
export const formatTimestamp = (ms: number): string => {
  const totalSeconds = Math.floor(ms / 1000);
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  const pad = (value: number) => String(value).padStart(2, "0");
  return hours > 0
    ? `${hours}:${pad(minutes)}:${pad(seconds)}`
    : `${minutes}:${pad(seconds)}`;
};

export const formatShare = (share: number): string =>
  `${Math.round(share * 1000) / 10}%`;

/** A reply gap, which is usually shorter than a second.
 *
 * formatDuration is second-granular and collapses every one of these to "0s":
 * real medians on a normal conversation sit between 200ms and 800ms, so the
 * only figure it could ever show was zero. Reply time is the one metric here
 * where sub-second resolution is the whole point.
 */
export const formatLatency = (ms: number): string => {
  if (ms < 1000) return `${Math.round(ms / 10) * 10}ms`;
  if (ms < 10_000) return `${(Math.round(ms / 100) / 10).toFixed(1)}s`;
  return formatDuration(ms);
};
