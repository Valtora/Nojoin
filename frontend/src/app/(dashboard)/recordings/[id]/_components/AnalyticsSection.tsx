"use client";

import { lazy, Suspense } from "react";

import type { RecordingId } from "@/types";

// Recharts is the largest dependency the app carries, and the recording route
// is opened far more often than this tab. Loading it only when the tab is
// first shown keeps it out of the route's initial bundle.
const AnalyticsView = lazy(() => import("@/components/analytics/AnalyticsView"));

interface AnalyticsSectionProps {
  active: boolean;
  recordingId: RecordingId;
  onPlaySegment?: (startMs: number) => void;
  onReviewSpeakers?: () => void;
}

export default function AnalyticsSection({
  active,
  recordingId,
  onPlaySegment,
  onReviewSpeakers,
}: AnalyticsSectionProps) {
  return (
    <div
      className={`absolute inset-0 flex flex-col ${active ? "z-10 visible" : "z-0 invisible"}`}
    >
      {/* Mounted only once the tab has been opened, so an unopened tab costs
          neither the chunk nor the request behind it. */}
      {active && (
        <Suspense
          fallback={
            <div className="flex h-full items-center justify-center text-sm text-contrast-helper">
              Loading analytics...
            </div>
          }
        >
          <AnalyticsView
            recordingId={recordingId}
            onPlaySegment={onPlaySegment}
            onReviewSpeakers={onReviewSpeakers}
          />
        </Suspense>
      )}
    </div>
  );
}
