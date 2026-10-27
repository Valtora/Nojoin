"use client";

import { lazy, Suspense } from "react";

import type { RecordingId } from "@/types";

// Recharts is the largest dependency the app carries, and the recording route
// is opened far more often than this tab. Loading it only when the tab is
// first shown keeps it out of the route's initial bundle.
const AnalyticsView = lazy(() => import("@/components/analytics/AnalyticsView"));
const LiveAnalyticsView = lazy(() => import("@/components/analytics/LiveAnalyticsView"));

interface AnalyticsSectionProps {
  active: boolean;
  inFlight?: boolean;
  refreshKey?: string;
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
  inFlight = false,
  refreshKey = "",
  recordingId,
  speakerColors,
  onPlaySegment,
  onReviewSpeakers,
}: AnalyticsSectionProps) {
  return (
    <div
      role="tabpanel"
      id="panel-analytics"
      aria-labelledby="tab-analytics"
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
          {inFlight ? <LiveAnalyticsView key={recordingId} recordingId={recordingId}
            refreshKey={refreshKey} speakerColors={speakerColors} /> : <AnalyticsView
            key={recordingId}
            recordingId={recordingId}
            speakerColors={speakerColors}
            onPlaySegment={onPlaySegment}
            onReviewSpeakers={onReviewSpeakers}
          />}
        </Suspense>
      )}
    </div>
  );
}
