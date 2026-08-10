"use client";

import type { AnalyticsAudioOverlap, AnalyticsAudioOverlapStatus } from "@/types";

import { chartColor } from "./chartPalette";
import { formatDuration, formatShare } from "./formatDuration";

/** Overlapping speech measured from the audio, shown without attribution.
 *
 * Deliberately no per-speaker figures and no "interruption" language: overlap
 * detection is reliable about *that and when* people talked at once, and
 * unreliable about who did it to whom or what it meant. Overlap includes
 * supportive back-channel talk as well as competition for the floor, and no
 * method, human or machine, reliably tells those apart. The total is a floor:
 * detection misses some overlap and never invents any.
 */

const DENSITY_BINS = 48;

interface OverlapPanelProps {
  overlap: AnalyticsAudioOverlap | null;
  status: AnalyticsAudioOverlapStatus;
  errorMessage: string | null;
  generating: boolean;
}

const densityBins = (overlap: AnalyticsAudioOverlap): number[] => {
  const bins = new Array<number>(DENSITY_BINS).fill(0);
  const span = Math.max(overlap.duration_ms, 1);
  const binMs = span / DENSITY_BINS;
  for (const [start, end] of overlap.regions) {
    const first = Math.min(Math.floor(start / binMs), DENSITY_BINS - 1);
    const last = Math.min(Math.floor(Math.max(end - 1, 0) / binMs), DENSITY_BINS - 1);
    for (let index = first; index <= last; index += 1) {
      const binStart = index * binMs;
      const binEnd = binStart + binMs;
      bins[index] += Math.max(
        0,
        Math.min(end, binEnd) - Math.max(start, binStart),
      );
    }
  }
  const peak = Math.max(...bins, 1);
  return bins.map((value) => value / peak);
};

export default function OverlapPanel({
  overlap,
  status,
  errorMessage,
  generating,
}: OverlapPanelProps) {
  if (status === "generating" || generating) {
    return (
      <p className="text-xs text-contrast-helper" role="status">
        Listening for overlapping speech. This reads the meeting&apos;s audio
        and takes a moment.
      </p>
    );
  }

  if (status === "error") {
    return (
      <p className="text-xs text-contrast-helper">
        {errorMessage ??
          "Overlapping speech could not be measured for this recording."}
      </p>
    );
  }

  if (!overlap || status !== "completed") {
    return (
      <p className="text-xs text-contrast-helper">
        Not measured yet. <strong>Measure delivery</strong> above also listens
        for overlapping speech.
      </p>
    );
  }

  const bins = densityBins(overlap);

  return (
    <div className="space-y-3">
      <p className="text-sm text-contrast-muted">
        People spoke over each other for at least{" "}
        <span className="font-semibold tabular-nums text-foreground">
          {formatDuration(overlap.total_overlap_ms)}
        </span>{" "}
        of this meeting
        <span className="tabular-nums">
          {" "}
          ({formatShare(overlap.overlap_share_of_audio)})
        </span>
        , across {overlap.region_count} moment
        {overlap.region_count === 1 ? "" : "s"}.
      </p>
      {overlap.region_count > 0 && (
        <div>
          <div
            className="flex h-8 items-end gap-px"
            role="img"
            aria-label="Where overlapping speech clustered across the meeting"
          >
            {bins.map((value, index) => (
              <span
                key={index}
                className="min-h-px flex-1 rounded-sm"
                style={{
                  height: `${Math.round(value * 100)}%`,
                  backgroundColor:
                    value > 0 ? chartColor(0) : "transparent",
                }}
              />
            ))}
          </div>
          <p className="mt-1 flex justify-between text-[10px] text-contrast-helper">
            <span>Start</span>
            <span>End</span>
          </p>
        </div>
      )}
      <p className="text-xs text-contrast-helper">
        Detected from the audio itself, so it works even though the transcript
        writes speech down one line at a time. The figure is a floor: detection
        misses some overlap and never invents any. Talking over each other is
        not the same as interrupting — it includes agreement, encouragement,
        and finishing each other&apos;s sentences — so Nojoin reports where it
        happened and does not guess who did it to whom.
      </p>
    </div>
  );
}
