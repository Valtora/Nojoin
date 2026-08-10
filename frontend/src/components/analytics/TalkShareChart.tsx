"use client";

import type { AnalyticsMetrics, AnalyticsSpeaker } from "@/types";

import { formatDuration, formatShare } from "./formatDuration";

interface TalkShareChartProps {
  speakers: AnalyticsSpeaker[];
  metrics: AnalyticsMetrics;
  colors: Record<string, string>;
}

/** Talk share, as a directly labelled bar per speaker.
 *
 * Hand-rolled rather than a chart library's horizontal bar chart, which is
 * what this was. A charting bar needs a fixed category axis and a fixed label
 * gutter, and those two reservations left about 90px of drawable bar once this
 * panel had to share a wide viewport with the timeline beside it. A bar whose
 * track is the full width of the panel reads at any width the tab can be.
 *
 * The bars are scaled 0-100%, not to the longest bar. Scaling to the leader
 * would make whoever spoke most look like they spoke all of it, and the shape
 * of the answer -- one long bar and five short ones, or five even ones -- is
 * the entire reason to look at this panel.
 */
export default function TalkShareChart({
  speakers,
  metrics,
  colors,
}: TalkShareChartProps) {
  if (!speakers.length) return null;

  return (
    <ul className="space-y-2.5">
      {speakers.map((speaker) => {
        const figures = metrics.talk_time[speaker.speaker_key];
        const share = figures?.share_of_speech ?? 0;
        return (
          <li key={speaker.speaker_key}>
            <div className="flex items-baseline gap-2 text-xs">
              <span
                className="h-2 w-2 shrink-0 self-center rounded-full"
                style={{ backgroundColor: colors[speaker.speaker_key] }}
                aria-hidden="true"
              />
              <span className="truncate text-foreground">{speaker.name}</span>
              <span className="ml-auto shrink-0 font-semibold tabular-nums text-foreground">
                {formatShare(share)}
              </span>
              <span className="shrink-0 tabular-nums text-contrast-helper">
                {formatDuration(figures?.speech_ms ?? 0)}
              </span>
            </div>
            <div className="mt-1 h-2 overflow-hidden rounded-full bg-surface-inset">
              <div
                className="h-full rounded-full"
                style={{
                  width: `${Math.max(share * 100, share > 0 ? 1 : 0)}%`,
                  backgroundColor: colors[speaker.speaker_key],
                }}
              />
            </div>
          </li>
        );
      })}
    </ul>
  );
}
