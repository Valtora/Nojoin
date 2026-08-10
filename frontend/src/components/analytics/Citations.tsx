"use client";

import type { AnalyticsCitation } from "@/types";

import { formatTimestamp } from "./formatDuration";

interface CitationsProps {
  citations: AnalyticsCitation[];
  onPlaySegment?: (startMs: number) => void;
}

/** The evidence behind a claim, and the way to check it against the audio.
 *
 * Never optional on the surfaces that use it. An item reaches the interface
 * only if at least one quote was found verbatim in the transcript, so an empty
 * list here means the component was given something it should not have been.
 */
export default function Citations({ citations, onPlaySegment }: CitationsProps) {
  if (!citations.length) return null;

  return (
    <ul className="mt-2 space-y-1.5">
      {citations.map((citation, index) => (
        <li
          key={`${citation.start_ms}-${index}`}
          className="border-l-2 border-surface-divider pl-2.5 text-xs text-contrast-helper"
        >
          <span className="italic">&ldquo;{citation.quote}&rdquo;</span>
          {citation.start_ms !== null &&
            (onPlaySegment ? (
              <button
                type="button"
                onClick={() => onPlaySegment(citation.start_ms as number)}
                className="ml-1.5 tabular-nums text-action-text hover:text-action-text-hover hover:underline"
                title={`Play from ${formatTimestamp(citation.start_ms)}`}
              >
                {formatTimestamp(citation.start_ms)}
              </button>
            ) : (
              <span className="ml-1.5 tabular-nums">
                {formatTimestamp(citation.start_ms)}
              </span>
            ))}
        </li>
      ))}
    </ul>
  );
}
