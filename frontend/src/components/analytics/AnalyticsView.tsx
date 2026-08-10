"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
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
import TurnsPanel from "./TurnsPanel";
import { Band, Section, StatTile } from "./Section";
import { buildSpeakerColors } from "./speakerPalette";
import { formatDuration, formatShare } from "./formatDuration";

interface AnalyticsViewProps {
  recordingId: RecordingId;
  /** Seek the player, so a named monologue can be listened to. */
  onPlaySegment?: (startMs: number) => void;
  onReviewSpeakers?: () => void;
  /**
   * The meeting's speaker colours, keyed by every alias a speaker answers to.
   * Supplied by the recording view, which is the only place they exist: they
   * are derived from the transcript client-side, and the payload's own `color`
   * field carries one only once a user has chosen it explicitly.
   */
  speakerColors?: Record<string, string>;
}

export default function AnalyticsView({
  recordingId,
  onPlaySegment,
  onReviewSpeakers,
  speakerColors,
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

  const speakers = analytics?.speakers;
  // Colour identifies a person, so it is resolved once for the whole tab and
  // handed to every panel. Deriving it per panel is how the tab ended up
  // disagreeing with the rest of the meeting view in the first place.
  const colors = useMemo(
    () => buildSpeakerColors(speakers ?? [], speakerColors),
    [speakers, speakerColors],
  );

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

  const { metrics } = analytics;
  const talkTimeMs = Object.fromEntries(
    Object.entries(metrics.talk_time).map(([key, figures]) => [
      key,
      figures.speech_ms,
    ]),
  );
  const hasTimeline = metrics.timeline.buckets.length >= 2;

  return (
    // A container, not a viewport breakpoint. This tab lives in a resizable
    // panel between two collapsible rails, so how much width it has is the
    // window minus a number it cannot see. See DESIGN.md.
    <div className="custom-scrollbar @container/tab h-full space-y-5 overflow-y-auto p-4">
      {analytics.attribution_warning && (
        <AttributionWarning
          warning={analytics.attribution_warning}
          speakers={analytics.speakers}
          onReviewSpeakers={onReviewSpeakers}
        />
      )}

      <Band className="grid-cols-2 gap-3 @lg/tab:grid-cols-4">
        <StatTile
          label="Speaking time"
          value={formatDuration(metrics.silence.speech_ms)}
        />
        <StatTile label="Speakers" value={String(analytics.speakers.length)} />
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
      </Band>

      {/* Share, and share over time: two views of one subject, so they share a
          row. The timeline takes the larger share of it because it carries a
          time axis, while a bar and its label read at any width. */}
      <Band className={hasTimeline ? "@4xl/tab:grid-cols-12" : undefined}>
        <Section
          title="Who spoke"
          hint="Share of everyone's speaking time. Overlapping speech counts for each person, so this is a share of speech rather than of the meeting's length."
          className={hasTimeline ? "@4xl/tab:col-span-5" : undefined}
        >
          <TalkShareChart
            speakers={analytics.speakers}
            metrics={metrics}
            colors={colors}
          />
        </Section>

        {hasTimeline && (
          <Section
            title="Who spoke when"
            hint="Speaking time per speaker across the meeting."
            className="@4xl/tab:col-span-7"
          >
            <TalkShareTimeline
              speakers={analytics.speakers}
              metrics={metrics}
              colors={colors}
            />
          </Section>
        )}
      </Band>

      {/* Both sides of how the floor changed hands: who took it and how
          quickly, and how much of the meeting had two people holding it. */}
      <Band className="@5xl/tab:grid-cols-12">
        <Section
          title="How the conversation moved"
          hint="A long median turn means holding the floor; a short one means dialogue."
          className="@5xl/tab:col-span-7"
        >
          <TurnsPanel
            speakers={analytics.speakers}
            metrics={metrics}
            colors={colors}
            onPlaySegment={onPlaySegment}
          />
        </Section>

        <Section
          title="Talking over each other"
          hint="Detected from the audio, because the transcript writes speech down one line at a time."
          className="@5xl/tab:col-span-5"
        >
          <OverlapPanel
            overlap={analytics.audio_overlap}
            status={analytics.audio_overlap_status}
            errorMessage={analytics.audio_overlap_error_message}
            generating={deliveryBusy}
          />
        </Section>
      </Band>

      <Band>
        <Section
          title="How people spoke"
          hint="Measured from the audio. This describes how someone spoke, not how they felt."
        >
          <DeliveryPanel
            delivery={analytics.delivery}
            status={analytics.delivery_status}
            errorMessage={analytics.delivery_error_message}
            stale={analytics.delivery_stale}
            speakers={analytics.speakers}
            talkTimeMs={talkTimeMs}
            baselines={analytics.delivery_baselines}
            colors={colors}
            onGenerate={() => void handleGenerate()}
            generating={deliveryBusy}
          />
        </Section>
      </Band>

      <MeetingAnalysisPanel
        ai={analytics.ai}
        status={analytics.ai_status}
        errorMessage={analytics.ai_error_message}
        stale={analytics.ai_stale}
        speakers={analytics.speakers}
        colors={colors}
        onGenerate={() => void handleAnalyse()}
        generating={aiBusy}
        onPlaySegment={onPlaySegment}
      />
    </div>
  );
}
