"use client";

import type { AnalyticsMetrics, AnalyticsSpeaker } from "@/types";

import { Note } from "./Section";
import {
  formatDuration,
  formatLatency,
  formatTimestamp,
} from "./formatDuration";

interface TurnsPanelProps {
  speakers: AnalyticsSpeaker[];
  metrics: AnalyticsMetrics;
  /** This meeting's speaker colours, so a person is one colour throughout. */
  colors: Record<string, string>;
  /** Seek the player, so a named monologue can be listened to. */
  onPlaySegment?: (startMs: number) => void;
}

export default function TurnsPanel({
  speakers,
  metrics,
  colors,
  onPlaySegment,
}: TurnsPanelProps) {
  return (
    <div>
      <div className="@container overflow-x-auto">
        <table className="analytics-table w-full text-sm">
          <thead>
            <tr className="border-b border-surface-divider text-left text-xs text-contrast-helper">
              <th scope="col" className="pb-2 font-medium">
                Speaker
              </th>
              <th scope="col" className="pb-2 text-right font-medium">
                Turns
              </th>
              <th scope="col" className="pb-2 text-right font-medium">
                Median turn
              </th>
              <th scope="col" className="pb-2 text-right font-medium">
                Longest turn
              </th>
              <th scope="col" className="pb-2 text-right font-medium">
                Instant handovers
              </th>
              <th scope="col" className="pb-2 text-right font-medium">
                Replies after
              </th>
            </tr>
          </thead>
          <tbody>
            {speakers.map((speaker) => {
              const structure = metrics.turn_structure[speaker.speaker_key];
              const latency =
                metrics.turn_taking.response_latency[speaker.speaker_key];
              return (
                <tr
                  key={speaker.speaker_key}
                  className="border-b border-surface-divider last:border-0"
                >
                  <td className="py-2">
                    <span className="flex items-center gap-2">
                      <span
                        className="h-2 w-2 shrink-0 rounded-full"
                        style={{ backgroundColor: colors[speaker.speaker_key] }}
                        aria-hidden="true"
                      />
                      <span className="truncate font-medium text-foreground">
                        {speaker.name}
                      </span>
                    </span>
                  </td>
                  <td
                    data-label="Turns"
                    className="py-2 text-right tabular-nums text-contrast-muted"
                  >
                    {structure?.turn_count ?? 0}
                  </td>
                  <td
                    data-label="Median turn"
                    className="py-2 text-right tabular-nums text-contrast-muted"
                  >
                    {formatDuration(structure?.median_turn_ms ?? 0)}
                  </td>
                  <td
                    data-label="Longest turn"
                    className="py-2 text-right tabular-nums"
                  >
                    {structure && onPlaySegment ? (
                      <button
                        type="button"
                        onClick={() =>
                          onPlaySegment(structure.longest_turn_start_ms)
                        }
                        className="text-action-text hover:text-action-text-hover hover:underline"
                        title={`Play from ${formatTimestamp(structure.longest_turn_start_ms)}`}
                      >
                        {formatDuration(structure.longest_turn_ms)}
                      </button>
                    ) : (
                      <span className="text-contrast-muted">
                        {formatDuration(structure?.longest_turn_ms ?? 0)}
                      </span>
                    )}
                  </td>
                  <td
                    data-label="Instant handovers"
                    className="py-2 text-right tabular-nums text-contrast-muted"
                  >
                    {latency?.immediate_count ?? 0}
                  </td>
                  <td
                    data-label="Replies after"
                    className="py-2 text-right tabular-nums text-contrast-muted"
                  >
                    {latency?.median_ms != null
                      ? formatLatency(latency.median_ms)
                      : "-"}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <Note label="How turns and reply times are measured">
        <p>
          An instant handover is taking the floor within a quarter of a second
          of the previous speaker stopping &mdash; quicker than the timing can
          measure, and usually a reply prepared while the other person was still
          talking.
        </p>
        <p>
          Reply time is the median over gaps long enough to measure. A turn
          taken after more than five seconds of silence is a fresh start rather
          than a reply, so it is left out. Treat differences under a quarter of
          a second as noise.
        </p>
      </Note>
    </div>
  );
}
