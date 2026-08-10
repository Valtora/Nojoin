"use client";

import { AlertTriangle } from "lucide-react";

import type { AnalyticsAttributionWarning, AnalyticsSpeaker } from "@/types";

interface AttributionWarningProps {
  warning: AnalyticsAttributionWarning;
  speakers: AnalyticsSpeaker[];
  onReviewSpeakers?: () => void;
}

const namesFor = (
  speakerKeys: string[] | undefined,
  speakers: AnalyticsSpeaker[],
): string => {
  if (!speakerKeys?.length) return "";
  const byKey = new Map(speakers.map((s) => [s.speaker_key, s.name]));
  return speakerKeys.map((key) => byKey.get(key) ?? key).join(", ");
};

// The wording lives here rather than in the API so the backend can add a
// reason code without shipping user-facing copy, and so the same codes can be
// phrased differently for an assistant than for a person.
const describe = (
  reason: AnalyticsAttributionWarning["reasons"][number],
  speakers: AnalyticsSpeaker[],
): string => {
  switch (reason.code) {
    case "low_share_clusters":
      return `${reason.speaker_count} speakers hold under 3% of the talking each. If one person was split into several, these shares are wrong.`;
    case "high_overlap":
      return `People talked over each other for ${Math.round((reason.overlap_share ?? 0) * 100)}% of the speech. Attribution is least reliable where voices overlap.`;
    case "speaker_cap_bound":
      return `This meeting reached its speaker limit of ${reason.max_speakers}. Speakers beyond the limit were merged into the ones shown.`;
    case "unnamed_speakers":
      return `Not everyone has been named yet: ${namesFor(reason.speaker_keys, speakers)}.`;
    default:
      return "";
  }
};

export default function AttributionWarning({
  warning,
  speakers,
  onReviewSpeakers,
}: AttributionWarningProps) {
  const messages = warning.reasons
    .map((reason) => describe(reason, speakers))
    .filter(Boolean);

  if (!messages.length) return null;

  return (
    <div className="rounded-lg border border-status-warning-border bg-status-warning-bg p-3 text-sm text-status-warning-fg">
      <div className="flex items-start gap-2">
        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
        <div className="min-w-0 space-y-1">
          <p className="font-medium">
            These figures depend on who Nojoin thinks was speaking
          </p>
          <ul className="list-disc space-y-0.5 pl-4">
            {messages.map((message) => (
              <li key={message}>{message}</li>
            ))}
          </ul>
          {onReviewSpeakers && (
            <button
              type="button"
              onClick={onReviewSpeakers}
              className="mt-1 font-medium underline underline-offset-2 hover:no-underline"
            >
              Review speakers
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
