"use client";

import type { AnalyticsAiDecision, AnalyticsSpeaker } from "@/types";

import Citations from "./Citations";
import { buildSpeakerLookup, consensusClass, consensusLabel } from "./aiTone";

interface AiDecisionsProps {
  decisions: AnalyticsAiDecision[];
  speakers: AnalyticsSpeaker[];
  onPlaySegment?: (startMs: number) => void;
}

export default function AiDecisions({
  decisions,
  speakers,
  onPlaySegment,
}: AiDecisionsProps) {
  if (!decisions.length) {
    return (
      <p className="text-xs text-contrast-helper">
        No decisions with quotable ownership were identified. The Notes tab
        remains the record of what was decided.
      </p>
    );
  }

  const lookup = buildSpeakerLookup(speakers);
  const names = (keys: string[]) => keys.map(lookup.name).join(", ");

  return (
    <div className="space-y-4">
      {decisions.map((decision, index) => (
        <div
          key={`${decision.decision}-${index}`}
          className="border-l-2 border-surface-divider pl-3"
        >
          <div className="flex flex-wrap items-start gap-2">
            <p className="min-w-0 flex-1 text-sm text-foreground">
              {decision.decision}
            </p>
            <span
              className={`shrink-0 rounded-full border px-2 py-0.5 text-xs font-medium ${consensusClass(decision.consensus)}`}
            >
              {consensusLabel(decision.consensus)}
            </span>
          </div>

          <dl className="mt-1.5 space-y-0.5 text-xs">
            {decision.proposed_by && (
              <div className="flex gap-1.5">
                <dt className="text-contrast-helper">Proposed by</dt>
                <dd className="text-contrast-muted">
                  {lookup.name(decision.proposed_by)}
                </dd>
              </div>
            )}
            {decision.agreed_by.length > 0 && (
              <div className="flex gap-1.5">
                <dt className="text-contrast-helper">Agreed</dt>
                <dd className="text-contrast-muted">
                  {names(decision.agreed_by)}
                </dd>
              </div>
            )}
            {decision.objected_by.length > 0 && (
              <div className="flex gap-1.5">
                <dt className="text-contrast-helper">Pushed back</dt>
                <dd className="text-contrast-muted">
                  {names(decision.objected_by)}
                </dd>
              </div>
            )}
          </dl>

          <Citations
            citations={decision.citations}
            onPlaySegment={onPlaySegment}
          />
        </div>
      ))}

      <p className="text-xs text-contrast-helper">
        This describes who owned each decision, not what was decided &mdash; the
        Notes tab is the record of that. Only speakers whose own words show it
        are listed as agreeing or pushing back, and every entry carries a quote
        you can play back. <span className="font-medium">Unchallenged</span>{" "}
        means nobody objected, which is not the same as agreement.
      </p>
    </div>
  );
}
