"use client";

import { Info, Loader2 } from "lucide-react";

import type {
  AnalyticsDelivery,
  AnalyticsDeliveryStatus,
  AnalyticsSpeaker,
} from "@/types";

import { chartColor } from "./chartPalette";

interface DeliveryPanelProps {
  delivery: AnalyticsDelivery | null;
  status: AnalyticsDeliveryStatus;
  errorMessage: string | null;
  stale: boolean;
  speakers: AnalyticsSpeaker[];
  onGenerate: () => void;
  generating: boolean;
}

const Empty = ({
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
        {busy && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
        {actionLabel}
      </button>
    )}
  </div>
);

export default function DeliveryPanel({
  delivery,
  status,
  errorMessage,
  stale,
  speakers,
  onGenerate,
  generating,
}: DeliveryPanelProps) {
  if (status === "generating" || generating) {
    return (
      <Empty message="Measuring how people spoke. This reads the meeting's audio and takes a moment." />
    );
  }

  if (status === "error") {
    return (
      <Empty
        message={errorMessage || "Delivery could not be measured."}
        actionLabel="Try again"
        onAction={onGenerate}
      />
    );
  }

  if (!delivery || status !== "completed") {
    return (
      <Empty
        message="Delivery is measured from the meeting's audio, so it is done on request rather than for every meeting."
        actionLabel="Measure delivery"
        onAction={onGenerate}
        busy={generating}
      />
    );
  }

  const measured = speakers.filter(
    (speaker) => delivery.speakers[speaker.speaker_key],
  );

  if (!measured.length) {
    return (
      <Empty message="Nobody in this meeting had enough uninterrupted speech to measure delivery from." />
    );
  }

  return (
    <div className="space-y-3">
      {stale && (
        <p className="rounded-lg border border-status-warning-border bg-status-warning-bg px-3 py-2 text-xs text-status-warning-fg">
          The transcript has changed since this was measured.{" "}
          <button
            type="button"
            onClick={onGenerate}
            className="font-medium underline underline-offset-2 hover:no-underline"
          >
            Measure again
          </button>
        </p>
      )}

      <div className="overflow-x-auto">
        <table className="w-full min-w-[34rem] text-sm">
          <thead>
            <tr className="border-b border-surface-divider text-left text-xs text-contrast-helper">
              <th scope="col" className="pb-2 font-medium">
                Speaker
              </th>
              <th scope="col" className="pb-2 text-right font-medium">
                Pace
              </th>
              <th scope="col" className="pb-2 text-right font-medium">
                Pitch
              </th>
              <th scope="col" className="pb-2 text-right font-medium">
                Pitch movement
              </th>
              <th scope="col" className="pb-2 text-right font-medium">
                Pauses
              </th>
            </tr>
          </thead>
          <tbody>
            {measured.map((speaker) => {
              const figures = delivery.speakers[speaker.speaker_key];
              return (
                <tr
                  key={speaker.speaker_key}
                  className="border-b border-surface-divider last:border-0"
                >
                  <td className="py-2">
                    <span className="flex items-center gap-2">
                      <span
                        className="h-2 w-2 shrink-0 rounded-full"
                        style={{
                          backgroundColor: chartColor(
                            speakers.indexOf(speaker),
                          ),
                        }}
                        aria-hidden="true"
                      />
                      <span className="truncate text-foreground">
                        {speaker.name}
                      </span>
                    </span>
                  </td>
                  <td className="py-2 text-right tabular-nums text-contrast-muted">
                    {figures.words_per_minute
                      ? `${figures.words_per_minute} wpm`
                      : "-"}
                  </td>
                  <td className="py-2 text-right tabular-nums text-contrast-muted">
                    {figures.median_f0_hz ? `${figures.median_f0_hz} Hz` : "-"}
                  </td>
                  <td className="py-2 text-right tabular-nums text-contrast-muted">
                    {figures.pitch_spread_semitones
                      ? `${figures.pitch_spread_semitones} st`
                      : "-"}
                  </td>
                  <td className="py-2 text-right tabular-nums text-contrast-muted">
                    {figures.pause_count}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* Loudness is deliberately absent from the table above when it is not
          comparable. Showing a column the user cannot legitimately read across
          is worse than not showing it. */}
      {!delivery.cross_speaker_loudness_comparable && (
        <p className="flex items-start gap-2 text-xs text-contrast-helper">
          <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          <span>
            These speakers reached the recording through different audio
            sources, so how loud they sound reflects microphones and call
            quality rather than how loudly they spoke. Loudness is not compared
            here for that reason.
          </span>
        </p>
      )}

      <p className="text-xs text-contrast-helper">
        Measured from the audio: pace is words per minute, pitch movement is the
        spread in semitones, and pauses are silences within a speaker&apos;s own
        turn. These describe how someone spoke, not how they felt.
      </p>
    </div>
  );
}
