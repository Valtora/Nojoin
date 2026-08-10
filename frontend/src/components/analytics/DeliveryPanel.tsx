"use client";

import { Info, Loader2 } from "lucide-react";

import type {
  AnalyticsDeliveryBaseline,
  AnalyticsDelivery,
  AnalyticsDeliveryStatus,
  AnalyticsSpeaker,
} from "@/types";

import { chartColor } from "./chartPalette";
import { buildDeliveryReadings } from "./deliveryInsights";

interface DeliveryPanelProps {
  delivery: AnalyticsDelivery | null;
  status: AnalyticsDeliveryStatus;
  errorMessage: string | null;
  stale: boolean;
  speakers: AnalyticsSpeaker[];
  /** Speaking time per speaker, so a pause count can become a pause rate. */
  talkTimeMs: Record<string, number>;
  /** Each linked person's usual figures across their other measured meetings. */
  baselines: Record<string, AnalyticsDeliveryBaseline>;
  onGenerate: () => void;
  generating: boolean;
}

/** A figure with its plain-language reading underneath. */
const Figure = ({ value, reading }: { value: string; reading?: string | null }) => (
  <>
    <span className="tabular-nums text-contrast-muted">{value}</span>
    {reading && (
      <span className="mt-0.5 block text-xs font-normal text-contrast-helper">
        {reading}
      </span>
    )}
  </>
);

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
  talkTimeMs,
  baselines,
  onGenerate,
  generating,
}: DeliveryPanelProps) {
  if (status === "generating" || generating) {
    return (
      <p
        className="flex items-center gap-2 text-xs text-contrast-helper"
        role="status"
      >
        <Loader2
          className="h-3.5 w-3.5 shrink-0 animate-spin text-action-text"
          aria-hidden="true"
        />
        Measuring how people spoke. This reads the meeting&apos;s audio and takes
        a moment.
      </p>
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

  const readings = buildDeliveryReadings(delivery, talkTimeMs, baselines);

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
              const figures = delivery.speakers[speaker.speaker_key]!;
              const reading = readings[speaker.speaker_key]!;
              return (
                <tr
                  key={speaker.speaker_key}
                  className="border-b border-surface-divider last:border-0"
                >
                  <td className="py-2 align-top">
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
                    {reading.baseline && (
                      <span className="mt-0.5 block text-xs text-contrast-helper">
                        {reading.baseline}
                      </span>
                    )}
                  </td>
                  <td className="py-2 text-right align-top">
                    <Figure
                      value={
                        figures.words_per_minute
                          ? `${figures.words_per_minute} wpm`
                          : "-"
                      }
                      reading={reading.pace}
                    />
                  </td>
                  <td className="py-2 text-right align-top">
                    {/* No reading: a descriptor here would characterise
                        someone's voice rather than how they used it. */}
                    <Figure
                      value={
                        figures.median_f0_hz ? `${figures.median_f0_hz} Hz` : "-"
                      }
                    />
                  </td>
                  <td className="py-2 text-right align-top">
                    <Figure
                      value={
                        figures.pitch_spread_semitones
                          ? `${figures.pitch_spread_semitones} st`
                          : "-"
                      }
                      reading={reading.pitchMovement}
                    />
                  </td>
                  <td className="py-2 text-right align-top">
                    <Figure
                      value={
                        reading.pauseRate === null
                          ? String(figures.pause_count)
                          : `${figures.pause_count} (${reading.pauseRate.toFixed(1)}/min)`
                      }
                      reading={reading.pauses}
                    />
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
        turn, shown as a rate so a long contribution does not look like a
        hesitant one. The pace words are anchored on measured English speech:
        recorded conversation runs at roughly 160 to 200 words a minute by this
        measure, presentations and narration slower, so treat the words as a
        rough guide for meetings held in other languages. Pitch movement and
        pausing have no such yardstick &mdash; the same expressive range
        measures wider on a high voice than a low one &mdash; so those are
        compared with the other people in this meeting and, where Nojoin has
        measured enough of a person&apos;s meetings, with their own usual
        figures. All of it describes how someone spoke, not how they felt.
      </p>
    </div>
  );
}
