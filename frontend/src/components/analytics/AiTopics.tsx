"use client";

import type { AnalyticsAiTopic, AnalyticsSpeaker } from "@/types";

import { chartColor } from "./chartPalette";
import { formatDuration, formatTimestamp } from "./formatDuration";
import { buildSpeakerLookup } from "./aiTone";

interface AiTopicsProps {
  topics: AnalyticsAiTopic[];
  speakers: AnalyticsSpeaker[];
  onPlaySegment?: (startMs: number) => void;
}

export default function AiTopics({
  topics,
  speakers,
  onPlaySegment,
}: AiTopicsProps) {
  if (!topics.length) {
    return (
      <p className="text-xs text-contrast-helper">
        No distinct topics were identified in this meeting.
      </p>
    );
  }

  const lookup = buildSpeakerLookup(speakers);

  return (
    <ol className="space-y-3">
      {topics.map((topic, index) => {
        const start = topic.start_ms;
        const duration =
          topic.start_ms !== null && topic.end_ms !== null
            ? topic.end_ms - topic.start_ms
            : null;
        return (
          <li
            key={`${topic.title}-${index}`}
            className="border-l-2 border-surface-divider pl-3"
          >
            <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
              {start !== null &&
                (onPlaySegment ? (
                  <button
                    type="button"
                    onClick={() => onPlaySegment(start)}
                    className="shrink-0 tabular-nums text-xs text-action-text hover:text-action-text-hover hover:underline"
                    title={`Play from ${formatTimestamp(start)}`}
                  >
                    {formatTimestamp(start)}
                  </button>
                ) : (
                  <span className="shrink-0 tabular-nums text-xs text-contrast-helper">
                    {formatTimestamp(start)}
                  </span>
                ))}
              <span className="text-sm font-medium text-foreground">
                {topic.title}
              </span>
              {duration !== null && duration > 0 && (
                <span className="text-xs text-contrast-helper">
                  {formatDuration(duration)}
                </span>
              )}
            </div>

            {topic.summary && (
              <p className="mt-1 text-xs text-contrast-muted">{topic.summary}</p>
            )}

            <p className="mt-1 flex items-center gap-1.5 text-xs text-contrast-helper">
              {topic.contested ? (
                // Deliberately not a name. Two people driving a topic equally
                // is a real answer, and picking one of them would not be.
                <span>Led jointly, or by no one in particular</span>
              ) : topic.led_by ? (
                <>
                  <span
                    className="h-2 w-2 shrink-0 rounded-full"
                    style={{
                      backgroundColor: chartColor(lookup.index(topic.led_by)),
                    }}
                    aria-hidden="true"
                  />
                  <span>Led by {lookup.name(topic.led_by)}</span>
                </>
              ) : (
                <span>No clear lead</span>
              )}
              {topic.leadership_basis && !topic.contested && (
                <span className="text-contrast-helper">
                  &mdash; {topic.leadership_basis}
                </span>
              )}
            </p>
          </li>
        );
      })}
    </ol>
  );
}
