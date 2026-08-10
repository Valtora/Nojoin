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

/** A reply gap, which is usually around a second.
 *
 * formatDuration is second-granular and collapses every one of these to "0s",
 * while millisecond precision would overclaim: the timestamps behind these
 * gaps carry roughly a quarter-second of noise (the same reason diarisation
 * scoring uses a 250ms collar), so tenths of a second is the finest display
 * the measurement supports.
 */
export const formatLatency = (ms: number): string => {
  if (ms < 10_000) return `${(Math.round(ms / 100) / 10).toFixed(1)}s`;
  return formatDuration(ms);
};
