"use client";

import { lazy, Suspense } from "react";

import LiveAnalyticsView from "@/components/analytics/LiveAnalyticsView";
import { useLiveAnalytics } from "@/components/analytics/useLiveAnalytics";
import type { ConsentNoticeOutcome, RecordingId } from "@/types";

const CONSENT_LABELS: Record<ConsentNoticeOutcome, string> = {
  no_objections: "No objections",
  objected: "Someone objected",
  no_answer: "No answer recorded",
  not_shown: "Not shown (your preference)",
};

// Recharts is the largest dependency the app carries, and the recording route
// is opened far more often than this tab. Loading it only when the tab is
// first shown keeps it out of the route's initial bundle.
const AnalyticsView = lazy(() => import("@/components/analytics/AnalyticsView"));

interface AnalyticsSectionProps {
  active: boolean;
  inFlight?: boolean;
  recordingId: RecordingId;
  consentNoticeOutcome?: ConsentNoticeOutcome | null;
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
  recordingId,
  consentNoticeOutcome,
  speakerColors,
  onPlaySegment,
  onReviewSpeakers,
}: AnalyticsSectionProps) {
  // Keep the reader's last snapshot outside either view: completion and tab
  // switches stop its requests without discarding estimates still on screen.
  const { analytics, error, clearAnalytics } = useLiveAnalytics(recordingId, active && inFlight);
  const estimates = analytics ? (
    <LiveAnalyticsView analytics={analytics} error={inFlight ? error : null} speakerColors={speakerColors} />
  ) : undefined;

  return (
    <div
      role="tabpanel"
      id="panel-analytics"
      aria-labelledby="tab-analytics"
      className={`absolute inset-0 flex flex-col ${active ? "z-10 visible" : "z-0 invisible"}`}
    >
      {consentNoticeOutcome && (
        <p className="px-4 py-2 text-sm text-foreground">
          Recording notice: {CONSENT_LABELS[consentNoticeOutcome]}
        </p>
      )}
      {/* The final view and its chart chunk are mounted only while the tab is open. */}
      {active && (
        <Suspense
          fallback={estimates ?? (
            <div className="flex h-full items-center justify-center text-sm text-contrast-helper">
              Loading analytics...
            </div>
          )}
        >
          {inFlight ? <LiveAnalyticsView analytics={analytics} error={error}
            speakerColors={speakerColors} /> : <AnalyticsView
            key={recordingId}
            recordingId={recordingId}
            speakerColors={speakerColors}
            onPlaySegment={onPlaySegment}
            onReviewSpeakers={onReviewSpeakers}
            initialFallback={estimates}
            onLoaded={clearAnalytics}
          />}
        </Suspense>
      )}
    </div>
  );
}
