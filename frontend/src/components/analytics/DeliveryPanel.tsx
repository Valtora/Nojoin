"use client";

import { Info, Waves } from "lucide-react";

import type {
  AnalyticsDeliveryBaseline,
  AnalyticsDelivery,
  AnalyticsDeliveryStatus,
  AnalyticsSpeaker,
} from "@/types";

import { Note, Prompt, StaleBanner, Working } from "./Section";
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
  /** This meeting's speaker colours, so a person is one colour throughout. */
  colors: Record<string, string>;
  onGenerate: () => void;
  generating: boolean;
}

/** A figure with its plain-language reading underneath.
 *
 * One element rather than two siblings, because the cell it sits in becomes a
 * label-and-value flex row at narrow widths and a loose reading span would
 * land beside the value instead of under it.
 */
const Figure = ({
  value,
  reading,
}: {
  value: string;
  reading?: string | null;
}) => (
  <span className="block">
    <span className="tabular-nums text-contrast-muted">{value}</span>
    {reading && (
      <span className="mt-0.5 block text-xs font-normal text-contrast-helper">
        {reading}
      </span>
    )}
  </span>
);

export default function DeliveryPanel({
  delivery,
  status,
  errorMessage,
  stale,
  speakers,
  talkTimeMs,
  baselines,
  colors,
  onGenerate,
  generating,
}: DeliveryPanelProps) {
  if (status === "generating" || generating) {
    return (
      <Working message="Measuring how people spoke. This reads the meeting's audio and takes a moment." />
    );
  }

  if (status === "error") {
    return (
      <Prompt
        message={errorMessage || "Delivery could not be measured."}
        actionLabel="Try again"
        onAction={onGenerate}
      />
    );
  }

  if (!delivery || status !== "completed") {
    return (
      <Prompt
        message="Delivery is measured from the meeting's audio, so it is done on request rather than for every meeting."
        actionLabel="Measure delivery"
        actionIcon={<Waves className="h-3.5 w-3.5" aria-hidden="true" />}
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
      <Prompt message="Nobody in this meeting had enough uninterrupted speech to measure delivery from." />
    );
  }

  const readings = buildDeliveryReadings(delivery, talkTimeMs, baselines);

  return (
    <div className="space-y-3">
      {stale && (
        <StaleBanner
          message="The transcript has changed since this was measured."
          actionLabel="Measure again"
          onAction={onGenerate}
        />
      )}

      <div className="@container overflow-x-auto">
        <table className="analytics-table w-full text-sm">
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
                          backgroundColor: colors[speaker.speaker_key],
                        }}
                        aria-hidden="true"
                      />
                      <span className="truncate font-medium text-foreground">
                        {speaker.name}
                      </span>
                    </span>
                    {reading.baseline && (
                      <span className="mt-0.5 block text-xs text-contrast-helper">
                        {reading.baseline}
                      </span>
                    )}
                  </td>
                  <td data-label="Pace" className="py-2 text-right align-top">
                    <Figure
                      value={
                        figures.words_per_minute
                          ? `${figures.words_per_minute} wpm`
                          : "-"
                      }
                      reading={reading.pace}
                    />
                  </td>
                  <td data-label="Pitch" className="py-2 text-right align-top">
                    {/* No reading: a descriptor here would characterise
                        someone's voice rather than how they used it. */}
                    <Figure
                      value={
                        figures.median_f0_hz ? `${figures.median_f0_hz} Hz` : "-"
                      }
                    />
                  </td>
                  <td
                    data-label="Pitch movement"
                    className="py-2 text-right align-top"
                  >
                    <Figure
                      value={
                        figures.pitch_spread_semitones
                          ? `${figures.pitch_spread_semitones} st`
                          : "-"
                      }
                      reading={reading.pitchMovement}
                    />
                  </td>
                  <td data-label="Pauses" className="py-2 text-right align-top">
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
          is worse than not showing it, and this stays in plain sight rather
          than going behind the note below: it answers "why is there no
          loudness here?", which is a question asked of the table itself. */}
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

      <Note label="How these figures are measured">
        <p>
          <span className="font-medium text-contrast-muted">Pace</span> is words
          a minute. The words beside it are anchored on measured English speech:
          recorded conversation runs at roughly 160 to 200 words a minute by
          this measure, presentations and narration slower. Treat them as a
          rough guide for a meeting held in another language.
        </p>
        <p>
          <span className="font-medium text-contrast-muted">
            Pitch movement
          </span>{" "}
          is the spread of someone&apos;s pitch, in semitones.{" "}
          <span className="font-medium text-contrast-muted">Pauses</span> are
          silences within a speaker&apos;s own turn, shown as a rate so a long
          contribution does not look like a hesitant one.
        </p>
        <p>
          Neither has a yardstick to measure against &mdash; the same expressive
          range measures wider on a high voice than a low one &mdash; so those
          two are compared with the other people in this meeting and, where
          Nojoin has measured enough of a person&apos;s meetings, with their own
          usual figures.
        </p>
      </Note>
    </div>
  );
}
