"use client";

import type { AnalyticsAiSentiment, AnalyticsSpeaker } from "@/types";

import Citations from "./Citations";
import { buildSpeakerLookup, toneClass, toneLabel } from "./aiTone";
import { chartColor } from "./chartPalette";

interface AiSentimentProps {
  sentiment: AnalyticsAiSentiment[];
  speakers: AnalyticsSpeaker[];
  onPlaySegment?: (startMs: number) => void;
}

export default function AiSentiment({
  sentiment,
  speakers,
  onPlaySegment,
}: AiSentimentProps) {
  if (!sentiment.length) {
    return (
      <p className="text-xs text-contrast-helper">
        Nobody&apos;s words carried a clear enough position, with a quote to
        support it, to report here.
      </p>
    );
  }

  const lookup = buildSpeakerLookup(speakers);

  return (
    <div className="space-y-3">
      {sentiment.map((item) => (
        <div key={item.speaker_key}>
          <div className="flex flex-wrap items-center gap-2">
            <span className="flex items-center gap-2">
              <span
                className="h-2 w-2 shrink-0 rounded-full"
                style={{
                  backgroundColor: chartColor(lookup.index(item.speaker_key)),
                }}
                aria-hidden="true"
              />
              <span className="text-sm text-foreground">
                {lookup.name(item.speaker_key)}
              </span>
            </span>
            <span
              className={`rounded-full border px-2 py-0.5 text-xs font-medium ${toneClass(item.tone)}`}
            >
              {toneLabel(item.tone)}
            </span>
          </div>
          <p className="mt-1 text-xs text-contrast-muted">{item.summary}</p>
          <Citations citations={item.citations} onPlaySegment={onPlaySegment} />
        </div>
      ))}

      <p className="text-xs text-contrast-helper">
        Read from what people said, not from how they sounded. Nojoin does not
        detect emotion, and this is not combined with the measured delivery
        figures above &mdash; a person can make a positive point flatly, or a
        negative one warmly.
      </p>
    </div>
  );
}
