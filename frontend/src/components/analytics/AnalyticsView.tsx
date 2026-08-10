"use client";

import { useCallback, useEffect, useState } from "react";
import { BarChart3 } from "lucide-react";

import {
  generateRecordingAiAnalytics,
  generateRecordingAnalytics,
  getRecordingAnalytics,
} from "@/lib/api";
import type { RecordingAnalytics, RecordingId } from "@/types";

import AttributionWarning from "./AttributionWarning";
import DeliveryPanel from "./DeliveryPanel";
import MeetingAnalysisPanel from "./MeetingAnalysisPanel";
import OverlapPanel from "./OverlapPanel";
import TalkShareChart from "./TalkShareChart";
import TalkShareTimeline from "./TalkShareTimeline";
import { chartColor } from "./chartPalette";
import {
  formatDuration,
  formatLatency,
  formatShare,
  formatTimestamp,
} from "./formatDuration";

interface AnalyticsViewProps {
  recordingId: RecordingId;
  /** Seek the player, so a named monologue can be listened to. */
  onPlaySegment?: (startMs: number) => void;
  onReviewSpeakers?: () => void;
}

const Panel = ({
  title,
  description,
  children,
}: {
  title: string;
  description?: string;
  children: React.ReactNode;
}) => (
  <section className="rounded-lg border border-surface-border bg-surface-card p-4">
    <h3 className="text-sm font-semibold text-foreground">{title}</h3>
    {description && (
      <p className="mt-0.5 text-xs text-contrast-helper">{description}</p>
    )}
    <div className="mt-3">{children}</div>
  </section>
);

const StatTile = ({ label, value }: { label: string; value: string }) => (
  <div className="rounded-lg border border-surface-border bg-surface-card p-3">
    <p className="text-xs text-contrast-helper">{label}</p>
    <p className="mt-1 text-xl font-semibold tabular-nums text-foreground">
      {value}
    </p>
  </div>
);

export default function AnalyticsView({
  recordingId,
  onPlaySegment,
  onReviewSpeakers,
}: AnalyticsViewProps) {
  const [analytics, setAnalytics] = useState<RecordingAnalytics | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [generating, setGenerating] = useState(false);
  const [analysing, setAnalysing] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setAnalytics(await getRecordingAnalytics(recordingId));
    } catch {
      setError("Analytics could not be loaded for this meeting.");
    } finally {
      setLoading(false);
    }
  }, [recordingId]);

  useEffect(() => {
    void load();
  }, [load]);

  // Both generated tiers run on a worker, so the POST returns as soon as the
  // task is queued -- before the worker has claimed it and written
  // "generating". Clearing the local flag on the POST's own response therefore
  // dropped the user back to the button for however long that took, with no
  // sign anything was happening, and left nothing polling. The flag is instead
  // held until the server reports a status that is not "pending", so the busy
  // state covers the whole gap.
  const deliveryBusy = generating || analytics?.delivery_status === "generating";
  const aiBusy = analysing || analytics?.ai_status === "generating";

  useEffect(() => {
    if (!deliveryBusy && !aiBusy) return;
    const timer = setInterval(() => {
      void getRecordingAnalytics(recordingId)
        .then((next) => {
          setAnalytics(next);
          if (next.delivery_status !== "pending") setGenerating(false);
          if (next.ai_status !== "pending") setAnalysing(false);
        })
        .catch(() => {});
    }, 3000);
    return () => clearInterval(timer);
  }, [deliveryBusy, aiBusy, recordingId]);

  const handleGenerate = useCallback(async () => {
    setGenerating(true);
    try {
      await generateRecordingAnalytics(recordingId);
      setAnalytics(await getRecordingAnalytics(recordingId));
    } catch {
      setError("Delivery analysis could not be started.");
      setGenerating(false);
    }
  }, [recordingId]);

  const handleAnalyse = useCallback(async () => {
    setAnalysing(true);
    try {
      await generateRecordingAiAnalytics(recordingId);
      setAnalytics(await getRecordingAnalytics(recordingId));
    } catch {
      setError("The meeting analysis could not be started.");
      setAnalysing(false);
    }
  }, [recordingId]);

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-contrast-helper">
        Loading analytics...
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 p-6 text-center">
        <p className="text-sm text-contrast-helper">{error}</p>
        <button
          type="button"
          onClick={() => void load()}
          className="text-sm font-medium text-action-text hover:text-action-text-hover"
        >
          Try again
        </button>
      </div>
    );
  }

  // No attributed speech is the normal state for a recording that captured
  // nothing or has not been through diarisation, not an error to dress up.
  if (!analytics || !analytics.speakers.length) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 p-6 text-center">
        <BarChart3
          className="h-8 w-8 text-contrast-icon-muted"
          aria-hidden="true"
        />
        <p className="text-sm font-medium text-foreground">No analytics yet</p>
        <p className="max-w-sm text-xs text-contrast-helper">
          Analytics are built from the finished transcript. They appear once the
          meeting has been processed and speakers have been separated.
        </p>
      </div>
    );
  }

  const { metrics, speakers } = analytics;
  const talkTimeMs = Object.fromEntries(
    Object.entries(metrics.talk_time).map(([key, figures]) => [
      key,
      figures.speech_ms,
    ]),
  );

  return (
    <div className="custom-scrollbar h-full space-y-3 overflow-y-auto p-4">
      {analytics.attribution_warning && (
        <AttributionWarning
          warning={analytics.attribution_warning}
          speakers={speakers}
          onReviewSpeakers={onReviewSpeakers}
        />
      )}

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatTile
          label="Speaking time"
          value={formatDuration(metrics.silence.speech_ms)}
        />
        <StatTile label="Speakers" value={String(speakers.length)} />
        <StatTile
          label="Silence"
          value={formatShare(metrics.silence.silence_share)}
        />
        <StatTile
          label="Talked over"
          value={
            analytics.audio_overlap
              ? `≥${formatShare(analytics.audio_overlap.overlap_share_of_audio)}`
              : "–"
          }
        />
      </div>

      <Panel
        title="Who spoke"
        description="Share of everyone's speaking time. Overlapping speech counts for each person, so this is a share of speech rather than of the meeting's length."
      >
        <TalkShareChart speakers={speakers} metrics={metrics} />
      </Panel>

      {metrics.timeline.buckets.length >= 2 && (
        <Panel
          title="Who spoke when"
          description="Speaking time per speaker across the meeting."
        >
          <TalkShareTimeline speakers={speakers} metrics={metrics} />
        </Panel>
      )}

      <Panel
        title="How people spoke"
        description="Measured from the audio. This describes delivery, not mood."
      >
        <DeliveryPanel
          delivery={analytics.delivery}
          status={analytics.delivery_status}
          errorMessage={analytics.delivery_error_message}
          stale={analytics.delivery_stale}
          speakers={speakers}
          talkTimeMs={talkTimeMs}
          baselines={analytics.delivery_baselines}
          onGenerate={() => void handleGenerate()}
          generating={deliveryBusy}
        />
      </Panel>

      <Panel
        title="How the conversation moved"
        description="A long median turn means holding the floor; a short one means dialogue."
      >
        <div className="overflow-x-auto">
          <table className="w-full min-w-[36rem] text-sm">
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
              {speakers.map((speaker, index) => {
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
                          style={{ backgroundColor: chartColor(index) }}
                          aria-hidden="true"
                        />
                        <span className="truncate text-foreground">
                          {speaker.name}
                        </span>
                      </span>
                    </td>
                    <td className="py-2 text-right tabular-nums text-contrast-muted">
                      {structure?.turn_count ?? 0}
                    </td>
                    <td className="py-2 text-right tabular-nums text-contrast-muted">
                      {formatDuration(structure?.median_turn_ms ?? 0)}
                    </td>
                    <td className="py-2 text-right tabular-nums">
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
                    <td className="py-2 text-right tabular-nums text-contrast-muted">
                      {latency?.immediate_count ?? 0}
                    </td>
                    <td className="py-2 text-right tabular-nums text-contrast-muted">
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
        <p className="mt-2 text-xs text-contrast-helper">
          An instant handover is taking the floor within a quarter of a second
          of the previous speaker stopping — quicker than the timing can
          measure, and usually a reply prepared while the other person was
          still talking. Reply time is the median over gaps long enough to
          measure; turns taken after more than five seconds of silence are a
          fresh start rather than a reply, so they are left out. Treat
          differences under a quarter of a second as noise.
        </p>
      </Panel>

      <Panel
        title="Talking over each other"
        description="Detected from the audio, because the transcript writes speech down one line at a time."
      >
        <OverlapPanel
          overlap={analytics.audio_overlap}
          status={analytics.audio_overlap_status}
          errorMessage={analytics.audio_overlap_error_message}
          generating={deliveryBusy}
        />
      </Panel>

      <Panel
        title="What was discussed"
        description="Read from the transcript by AI, with quotes you can check. Everything above is measured; this is not."
      >
        <MeetingAnalysisPanel
          ai={analytics.ai}
          status={analytics.ai_status}
          errorMessage={analytics.ai_error_message}
          stale={analytics.ai_stale}
          speakers={speakers}
          onGenerate={() => void handleAnalyse()}
          generating={aiBusy}
          onPlaySegment={onPlaySegment}
        />
      </Panel>
    </div>
  );
}
