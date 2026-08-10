"use client";

import { Sparkles } from "lucide-react";

import type { AnalyticsAi, AnalyticsAiStatus, AnalyticsSpeaker } from "@/types";

import AiDecisions from "./AiDecisions";
import AiQuestions from "./AiQuestions";
import AiSentiment from "./AiSentiment";
import AiTopics from "./AiTopics";
import { Note, Prompt, StaleBanner, Working } from "./Section";
import { formatTimestamp } from "./formatDuration";

interface MeetingAnalysisPanelProps {
  ai: AnalyticsAi | null;
  status: AnalyticsAiStatus;
  errorMessage: string | null;
  stale: boolean;
  speakers: AnalyticsSpeaker[];
  /** This meeting's speaker colours, so a person is one colour throughout. */
  colors: Record<string, string>;
  onGenerate: () => void;
  generating: boolean;
  onPlaySegment?: (startMs: number) => void;
}

const Subsection = ({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) => (
  <section className="min-w-0">
    <h4 className="text-xs font-semibold uppercase tracking-wide text-contrast-helper">
      {title}
    </h4>
    <div className="mt-2">{children}</div>
  </section>
);

/** The AI tier, on its own surface.
 *
 * The band is the point: everything above it is measured from the recording,
 * and this is a model's reading of the transcript. With the panels above
 * flattened onto the tab's own card, an inset surface is what keeps that
 * boundary visible without reintroducing a card inside a card.
 */
export default function MeetingAnalysisPanel({
  ai,
  status,
  errorMessage,
  stale,
  speakers,
  colors,
  onGenerate,
  generating,
  onPlaySegment,
}: MeetingAnalysisPanelProps) {
  const body = () => {
    if (status === "generating" || generating) {
      // This run takes minutes and the request that starts it returns in
      // milliseconds, so without a visible working state the interface looks
      // as though nothing happened and invites a second click that would spend
      // the quota twice.
      return (
        <Working message="Analysing the meeting. Your AI provider is reading the whole transcript, which usually takes two to four minutes. You can leave this tab and come back." />
      );
    }

    // Not an error, and deliberately not dressed as one: an installation with
    // no AI provider is working correctly, and the rest of this tab is
    // unaffected.
    if (status === "unavailable") {
      return (
        <Prompt message="This needs an AI provider, and none is configured for this installation. Everything else on this tab is measured from the recording and works without one." />
      );
    }

    if (status === "error") {
      return (
        <Prompt
          message={errorMessage || "This meeting could not be analysed."}
          actionLabel="Try again"
          onAction={onGenerate}
          busy={generating}
        />
      );
    }

    if (!ai || status !== "completed") {
      return (
        <Prompt
          message="Nojoin can read the transcript for the topics it moved through, how people put things, which questions went unanswered, and who owned each decision. It uses your AI provider, so it runs when you ask for it."
          actionLabel="Analyse meeting"
          actionIcon={<Sparkles className="h-3.5 w-3.5" aria-hidden="true" />}
          onAction={onGenerate}
          busy={generating}
        />
      );
    }

    return (
      <div className="space-y-4">
        {stale && (
          <StaleBanner
            message="The transcript has changed since this was written."
            actionLabel="Analyse again"
            onAction={onGenerate}
          />
        )}

        {ai.transcript_truncated && (
          <p className="rounded-surface-subtle border border-status-warning-border bg-status-warning-bg px-3 py-2 text-xs text-status-warning-fg">
            This meeting was too long to analyse in full. Everything below
            covers the meeting up to {formatTimestamp(ai.analysed_through_ms)}{" "}
            only.
          </p>
        )}

        {/* Two columns of two, grouped by what they answer: what the meeting
            moved through and what went unanswered on one side, and the two
            per-person readings on the other. */}
        <div className="grid gap-x-6 gap-y-5 @3xl/tab:grid-cols-2">
          <div className="min-w-0 space-y-5">
            <Subsection title="What the meeting covered">
              <AiTopics
                topics={ai.topics}
                speakers={speakers}
                colors={colors}
                onPlaySegment={onPlaySegment}
              />
            </Subsection>
            <Subsection title="Questions asked">
              <AiQuestions
                questions={ai.questions}
                speakers={speakers}
                colors={colors}
                onPlaySegment={onPlaySegment}
              />
            </Subsection>
          </div>
          <div className="min-w-0 space-y-5">
            <Subsection title="How people put things">
              <AiSentiment
                sentiment={ai.sentiment}
                speakers={speakers}
                colors={colors}
                onPlaySegment={onPlaySegment}
              />
            </Subsection>
            <Subsection title="Who owned each decision">
              <AiDecisions
                decisions={ai.decisions}
                speakers={speakers}
                colors={colors}
                onPlaySegment={onPlaySegment}
              />
            </Subsection>
          </div>
        </div>

        <Note label="How to read this section">
          <p>
            Unlike the rest of this tab, this section is an AI model&apos;s
            reading of the transcript rather than a measurement, so treat it as
            a starting point and check the quotes. Anything it could not
            evidence with a quote from the transcript was discarded rather than
            shown.
          </p>
        </Note>
      </div>
    );
  };

  return (
    <div className="rounded-surface-panel bg-surface-inset p-4">
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
        <h3 className="flex items-center gap-2 text-sm font-semibold text-foreground">
          <Sparkles
            className="h-4 w-4 shrink-0 text-action-text"
            aria-hidden="true"
          />
          What was discussed
        </h3>
        <span className="rounded-full border border-surface-border px-2 py-0.5 text-[11px] font-medium text-contrast-helper">
          Read by AI, not measured
        </span>
      </div>
      <p className="mt-0.5 text-xs text-contrast-helper">
        Read from the transcript by AI, with quotes you can check. Everything
        above is measured; this is not.
      </p>
      <div className="mt-3">{body()}</div>
    </div>
  );
}
