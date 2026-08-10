"use client";

import type { AnalyticsAiQuestion, AnalyticsSpeaker } from "@/types";

import { buildSpeakerLookup } from "./aiTone";
import { formatTimestamp } from "./formatDuration";

interface AiQuestionsProps {
  questions: AnalyticsAiQuestion[];
  speakers: AnalyticsSpeaker[];
  colors: Record<string, string>;
  onPlaySegment?: (startMs: number) => void;
}

export default function AiQuestions({
  questions,
  speakers,
  colors,
  onPlaySegment,
}: AiQuestionsProps) {
  if (!questions.length) {
    return (
      <p className="text-xs text-contrast-helper">
        No substantive questions were identified in this meeting.
      </p>
    );
  }

  const lookup = buildSpeakerLookup(speakers, colors);
  // Unanswered questions lead. They are the reason to look at this section,
  // and burying them under answered ones defeats the point of the section.
  const ordered = [
    ...questions.filter((question) => !question.answered_by),
    ...questions.filter((question) => question.answered_by),
  ];
  const unanswered = questions.length - questions.filter((q) => q.answered_by).length;

  return (
    <div className="space-y-3">
      {unanswered > 0 && (
        <p className="text-xs text-contrast-helper">
          {unanswered} of {questions.length}{" "}
          {questions.length === 1 ? "question" : "questions"} went unanswered.
        </p>
      )}

      <ul className="space-y-3">
        {ordered.map((question, index) => (
          <li
            key={`${question.question}-${index}`}
            className="border-l-2 border-surface-divider pl-3"
          >
            <p className="text-sm text-foreground">{question.question}</p>
            <p className="mt-0.5 text-xs text-contrast-helper">
              <span
                className="mr-1.5 inline-block h-2 w-2 rounded-full align-middle"
                style={{ backgroundColor: lookup.color(question.asked_by) }}
                aria-hidden="true"
              />
              Asked by {lookup.name(question.asked_by)}
              {question.asked_at_ms !== null &&
                (onPlaySegment ? (
                  <button
                    type="button"
                    onClick={() => onPlaySegment(question.asked_at_ms as number)}
                    className="ml-1.5 tabular-nums text-action-text hover:text-action-text-hover hover:underline"
                    title={`Play from ${formatTimestamp(question.asked_at_ms)}`}
                  >
                    {formatTimestamp(question.asked_at_ms)}
                  </button>
                ) : (
                  <span className="ml-1.5 tabular-nums">
                    {formatTimestamp(question.asked_at_ms)}
                  </span>
                ))}
            </p>
            {question.answered_by ? (
              <p className="mt-1 text-xs text-contrast-muted">
                <span className="font-medium">
                  {lookup.name(question.answered_by)} answered
                </span>
                {question.answer_summary ? `: ${question.answer_summary}` : "."}
              </p>
            ) : (
              <p className="mt-1 text-xs font-medium text-status-warning-fg">
                Unanswered
              </p>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}
