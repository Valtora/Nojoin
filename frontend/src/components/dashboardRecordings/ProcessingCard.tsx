"use client";

import { SurfaceBase } from '@/components/ui/SurfaceBase';
import Link from "next/link";
import { Loader2 } from "lucide-react";

import { StatusBadge } from "@/components/ui/Badge";
import { Recording } from "@/types";
import RecordingAttentionBadge from "../RecordingAttentionBadge";

interface ProcessingCardProps {
  recordings: Recording[];
}

const formatEta = (seconds?: number | null): string | null => {
  if (!seconds || seconds <= 0) return null;
  const minutes = Math.max(1, Math.round(seconds / 60));
  return `about ${minutes} min left`;
};

/**
 * Whatever the pipeline is still working on.
 *
 * On the dashboard because "is that still running" is asked from wherever you
 * happen to be, not only from the recordings page.
 *
 * The caller hides this module when nothing is in flight, so it appears only
 * while there is something to report and disappears when the queue drains.
 */
export default function ProcessingCard({ recordings }: ProcessingCardProps) {
  return (
    <SurfaceBase data-ui-fill="card" data-ui-border="full" kind="card" className="density-surface flex h-full min-h-0 flex-col border border-surface-border bg-surface-card shadow-card">
      <div className="flex items-center gap-3">
        <Loader2 className="h-5 w-5 shrink-0 animate-spin text-action-text" />
        <h2 className="text-base font-semibold text-foreground">Processing</h2>
      </div>

      <ul className="mt-4 min-h-0 flex-1 space-y-2 overflow-y-auto">
        {recordings.map((recording) => {
          const eta = formatEta(recording.processing_eta_seconds);

          return (
            <li key={recording.id}>
              <Link
                href={`/recordings/${recording.id}`}
                className="density-surface-panel block bg-surface-inset px-3 py-2.5 transition-colors hover:bg-action-tint focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring"
              >
                <span className="flex flex-wrap items-center gap-3">
                  <span className="min-w-0 grow basis-40 truncate text-sm font-medium text-foreground">
                    {recording.name}
                  </span>
                  <RecordingAttentionBadge
                    recordingId={recording.id}
                    fallback={<StatusBadge status={recording.status} clientStatus={recording.client_status} size="sm" />}
                  />
                </span>
                {recording.processing_step || eta ? (
                  <span className="mt-1 block truncate text-xs text-contrast-helper">
                    {[recording.processing_step, eta].filter(Boolean).join(" · ")}
                  </span>
                ) : null}
              </Link>
            </li>
          );
        })}
      </ul>
    </SurfaceBase>
  );
}
