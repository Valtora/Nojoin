"use client";

import Link from "next/link";
import { Mic } from "lucide-react";

import { StatusBadge } from "@/components/ui/Badge";
import { formatTimeZoneDate } from "@/lib/timezone";
import { Recording } from "@/types";

interface RecentRecordingsCardProps {
  recordings: Recording[];
  timeZone: string;
}

const formatDuration = (seconds?: number): string | null => {
  if (!seconds || seconds <= 0) return null;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  return `${hours}h ${minutes % 60}m`;
};

/**
 * The last handful of recordings, with a click through to the detail view.
 *
 * The dashboard had no recordings presence at all: the rail that lists them
 * only renders on /recordings, so "what happened lately" was a navigation away.
 *
 * The caller hides this module when the list is empty, which is why there is no
 * empty state here.
 */
export default function RecentRecordingsCard({
  recordings,
  timeZone,
}: RecentRecordingsCardProps) {
  return (
    <div className="density-surface flex h-full min-h-0 flex-col border border-surface-border bg-surface-card shadow-card">
      <div className="flex items-center gap-3">
        <Mic className="h-5 w-5 shrink-0 text-action-text" />
        <h2 className="text-base font-semibold text-foreground">Recent recordings</h2>
      </div>

      <ul className="mt-4 min-h-0 flex-1 space-y-2 overflow-y-auto">
        {recordings.map((recording) => {
          const duration = formatDuration(recording.duration_seconds);

          return (
            <li key={recording.id}>
              <Link
                href={`/recordings/${recording.id}`}
                className="density-surface-panel flex items-center gap-3 bg-surface-inset px-3 py-2.5 transition-colors hover:bg-action-tint focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring"
              >
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-medium text-foreground">
                    {recording.name}
                  </span>
                  <span className="mt-0.5 block text-xs text-contrast-helper">
                    {formatTimeZoneDate(
                      new Date(recording.created_at),
                      timeZone,
                      "d MMM",
                    )}
                    {duration ? ` · ${duration}` : ""}
                  </span>
                </span>
                <StatusBadge status={recording.status} size="sm" />
              </Link>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
