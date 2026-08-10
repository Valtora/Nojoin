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
  /**
   * The meeting's speaker colours, so a person is the same colour here as in
   * the transcript and the speaker panel. They are derived client-side from
   * the transcript, so the analytics payload cannot supply them on its own.
   */
  speakerColors: Record<string, string>;
  onPlaySegment?: (startMs: number) => void;
  onReviewSpeakers?: () => void;
}

export default function AnalyticsSection({
  active,
  recordingId,
  speakerColors,
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
            speakerColors={speakerColors}
            onPlaySegment={onPlaySegment}
            onReviewSpeakers={onReviewSpeakers}
          />
        </Suspense>
      )}
    </div>
  );
}
