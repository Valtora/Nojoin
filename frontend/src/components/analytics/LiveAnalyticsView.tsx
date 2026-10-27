"use client";

import { useMemo } from "react";
import type { RecordingId } from "@/types";

import { Band, Note, Section, StatTile } from "./Section";
import { formatDuration, formatShare, formatTimestamp } from "./formatDuration";
import { buildSpeakerColors } from "./speakerPalette";
import { useLiveAnalytics } from "./useLiveAnalytics";

export default function LiveAnalyticsView({
  recordingId,
  refreshKey,
  speakerColors,
}: {
  recordingId: RecordingId;
  refreshKey: string;
  speakerColors: Record<string, string>;
}) {
  const { analytics, error } = useLiveAnalytics(recordingId, refreshKey);
  const colors = useMemo(() => buildSpeakerColors(analytics?.speakers ?? [], speakerColors),
    [analytics?.speakers, speakerColors]);

  return (
    <div className="custom-scrollbar @container/tab h-full space-y-5 overflow-y-auto p-4 pb-clear-floating-4">
      <p className="text-sm text-contrast-helper">
        Transcript estimates: wording, timings and speakers may still change.
      </p>
      {error && <p role="status" className="text-sm text-contrast-helper">{error}</p>}
      {!analytics ? (
        <p className="text-sm text-contrast-helper">Loading analytics...</p>
      ) : !analytics.speakers.length ? (
        <p className="text-sm text-contrast-helper">Estimates appear as speech arrives.</p>
      ) : (
        <Section title="Who spoke so far" hint="Estimated speaking time, share and longest turns from the transcript so far.">
          <div className="overflow-x-auto">
            <table className="analytics-table w-full text-sm">
              <thead>
                <tr className="border-b border-surface-divider text-left text-xs text-contrast-helper">
                  {['Speaker', 'Speaking time', 'Share of speech', 'Longest turn', 'Words per minute'].map((label) => (
                    <th key={label} scope="col" className="px-2 pb-2 font-medium">{label}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {analytics.speakers.map((speaker) => {
                  const figures = analytics.metrics.talk_time[speaker.speaker_key];
                  const turn = analytics.metrics.turn_structure[speaker.speaker_key];
                  const pace = analytics.transcript_wpm[speaker.speaker_key];
                  return (
                    <tr key={speaker.speaker_key} className="border-b border-surface-divider last:border-0">
                      <td className="px-2 py-2 font-medium text-foreground">
                        <span className="mr-2 inline-block h-2 w-2 rounded-full" aria-hidden="true"
                          style={{ backgroundColor: colors[speaker.speaker_key] }} />
                        {speaker.name}
                      </td>
                      <td data-label="Speaking time" className="px-2 py-2 tabular-nums text-contrast-muted">
                        {formatDuration(figures?.speech_ms ?? 0)}
                      </td>
                      <td data-label="Share of speech" className="px-2 py-2 tabular-nums text-contrast-muted">
                        {formatShare(figures?.share_of_speech ?? 0)}
                      </td>
                      <td data-label="Longest turn" className="px-2 py-2 tabular-nums text-contrast-muted"
                        title={turn ? `Starts at ${formatTimestamp(turn.longest_turn_start_ms)}` : undefined}>
                        {formatDuration(turn?.longest_turn_ms ?? 0)}
                      </td>
                      <td data-label="Words per minute" className="px-2 py-2 tabular-nums text-contrast-muted">
                        {pace?.words_per_minute ?? "Not enough speech yet"}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <Note label="How these estimates are measured">
            <p>Overlapping speech counts towards each speaker&apos;s time and share of speech.</p>
            <p>Pace is a transcript estimate: the median of eligible utterance rates. Short and overlapping utterances
              are excluded, and at least five eligible utterances are needed for a figure.</p>
          </Note>
        </Section>
      )}
      <Band className="grid-cols-2 gap-3 @lg/tab:grid-cols-4">
        {['Silence', 'Talked over', 'Audio delivery and pitch', 'AI analysis'].map((label) => (
          <StatTile key={label} label={label} value="After processing" />
        ))}
      </Band>
    </div>
  );
}
