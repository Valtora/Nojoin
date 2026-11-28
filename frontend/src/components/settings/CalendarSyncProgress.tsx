"use client";

import type { CalendarConnection } from "@/types";

/** The share of a sync done, 0 to 100, or null when it reports none yet. */
export function syncPercent(connection: CalendarConnection): number | null {
  const done = connection.sync_progress_done;
  const total = connection.sync_progress_total;
  if (done == null || !total) {
    return null;
  }
  return Math.min(100, Math.round((done / total) * 100));
}

/**
 * A calendar account's sync progress: a bar fed by the steps the sync has
 * stored (each calendar's windows of events), and what it means in words.
 */
export default function CalendarSyncProgress({
  connection,
}: {
  connection: CalendarConnection;
}) {
  const percent = syncPercent(connection);
  const label =
    percent === null
      ? "Starting the sync..."
      : `Syncing events: ${connection.sync_progress_done} of ${connection.sync_progress_total} steps`;
  return (
    <div className="space-y-1.5">
      <div
        role="progressbar"
        aria-label="Calendar sync progress"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={percent ?? undefined}
        aria-valuetext={label}
        className="h-2 w-full overflow-hidden rounded-full bg-action-tint"
      >
        <div
          className={
            percent === null
              ? "h-full w-1/4 animate-pulse rounded-full bg-action"
              : "h-full rounded-full bg-action transition-[width] duration-500"
          }
          style={percent === null ? undefined : { width: `${percent}%` }}
        />
      </div>
      <p className="text-xs contrast-helper">{label}</p>
    </div>
  );
}
