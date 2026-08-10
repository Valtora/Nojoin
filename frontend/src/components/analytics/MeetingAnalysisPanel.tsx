"use client";

import { Loader2, Sparkles } from "lucide-react";

import type { AnalyticsAi, AnalyticsAiStatus, AnalyticsSpeaker } from "@/types";

import AiDecisions from "./AiDecisions";
import AiQuestions from "./AiQuestions";
import AiSentiment from "./AiSentiment";
import AiTopics from "./AiTopics";
import { formatTimestamp } from "./formatDuration";

interface MeetingAnalysisPanelProps {
  ai: AnalyticsAi | null;
  status: AnalyticsAiStatus;
  errorMessage: string | null;
  stale: boolean;
  speakers: AnalyticsSpeaker[];
  onGenerate: () => void;
  generating: boolean;
  onPlaySegment?: (startMs: number) => void;
}

/** A working state that is visible the instant the user asks for it.
 *
 * This run takes minutes, and the request that starts it returns in
 * milliseconds, so without a spinner the interface looks as though nothing
 * happened and invites a second click that would spend the quota twice.
 */
const Working = ({ message }: { message: string }) => (
  <p
    className="flex items-center gap-2 text-xs text-contrast-helper"
    role="status"
  >
    <Loader2
      className="h-3.5 w-3.5 shrink-0 animate-spin text-action-text"
      aria-hidden="true"
    />
    {message}
  </p>
);

const State = ({
  message,
  actionLabel,
  onAction,
  busy,
}: {
  message: string;
  actionLabel?: string;
  onAction?: () => void;
  busy?: boolean;
}) => (
  <div className="flex flex-col items-start gap-2">
    <p className="text-xs text-contrast-helper">{message}</p>
    {actionLabel && onAction && (
      <button
        type="button"
        onClick={onAction}
        disabled={busy}
        className="inline-flex items-center gap-2 rounded-lg bg-action px-3 py-1.5 text-sm font-medium text-action-on transition-colors hover:bg-action-hover disabled:opacity-60"
      >
        {busy ? (
          <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
        ) : (
          <Sparkles className="h-3.5 w-3.5" aria-hidden="true" />
        )}
        {actionLabel}
      </button>
    )}
  </div>
);

const Section = ({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) => (
  <section>
    <h4 className="text-xs font-semibold uppercase tracking-wide text-contrast-helper">
      {title}
    </h4>
    <div className="mt-2">{children}</div>
  </section>
);

export default function MeetingAnalysisPanel({
  ai,
  status,
  errorMessage,
  stale,
  speakers,
  onGenerate,
  generating,
  onPlaySegment,
}: MeetingAnalysisPanelProps) {
  if (status === "generating" || generating) {
    return (
      <Working message="Analysing the meeting. Your AI provider is reading the whole transcript, which usually takes two to four minutes. You can leave this tab and come back." />
    );
  }

  // Not an error, and deliberately not dressed as one: an installation with no
  // AI provider is working correctly, and the rest of this tab is unaffected.
  if (status === "unavailable") {
    return (
      <State message="This needs an AI provider, and none is configured for this installation. Everything else on this tab is measured from the recording and works without one." />
    );
  }

  if (status === "error") {
    return (
      <State
        message={errorMessage || "This meeting could not be analysed."}
        actionLabel="Try again"
        onAction={onGenerate}
        busy={generating}
      />
    );
  }

  if (!ai || status !== "completed") {
    return (
      <State
        message="Nojoin can read the transcript for the topics it moved through, how people put things, which questions went unanswered, and who owned each decision. It uses your AI provider, so it runs when you ask for it."
        actionLabel="Analyse meeting"
        onAction={onGenerate}
        busy={generating}
      />
    );
  }

  return (
    <div className="space-y-5">
      {stale && (
        <p className="rounded-lg border border-status-warning-border bg-status-warning-bg px-3 py-2 text-xs text-status-warning-fg">
          The transcript has changed since this was written.{" "}
          <button
            type="button"
            onClick={onGenerate}
            className="font-medium underline underline-offset-2 hover:no-underline"
          >
            Analyse again
          </button>
        </p>
      )}

      {ai.transcript_truncated && (
        <p className="rounded-lg border border-status-warning-border bg-status-warning-bg px-3 py-2 text-xs text-status-warning-fg">
          This meeting was too long to analyse in full. Everything below covers
          the meeting up to {formatTimestamp(ai.analysed_through_ms)} only.
        </p>
      )}

      <Section title="What the meeting covered">
        <AiTopics
          topics={ai.topics}
          speakers={speakers}
          onPlaySegment={onPlaySegment}
        />
      </Section>

      <Section title="How people put things">
        <AiSentiment
          sentiment={ai.sentiment}
          speakers={speakers}
          onPlaySegment={onPlaySegment}
        />
      </Section>

      <Section title="Questions asked">
        <AiQuestions
          questions={ai.questions}
          speakers={speakers}
          onPlaySegment={onPlaySegment}
        />
      </Section>

      <Section title="Who owned each decision">
        <AiDecisions
          decisions={ai.decisions}
          speakers={speakers}
          onPlaySegment={onPlaySegment}
        />
      </Section>

      <p className="text-xs text-contrast-helper">
        Unlike the rest of this tab, this section is an AI model&apos;s reading
        of the transcript rather than a measurement, so treat it as a starting
        point and check the quotes. Anything it could not evidence with a quote
        from the transcript was discarded rather than shown.
      </p>
    </div>
  );
}
