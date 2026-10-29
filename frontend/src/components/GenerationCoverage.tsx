import { formatTimestamp } from "./analytics/formatDuration";

/** Discloses the transcript snapshot used by an independently generated output. */
export default function GenerationCoverage({ metadata }: {
  metadata?: { generated_through_ms?: number } | null;
}) {
  const through = metadata?.generated_through_ms;
  if (through === undefined || !Number.isFinite(through)) return null;
  return <p className="px-4 py-2 text-xs text-contrast-helper">
    Based on the transcript through {formatTimestamp(through)}. Regenerate to include later speech.
  </p>;
}
