import type {
  AnalyticsConsensus,
  AnalyticsSpeaker,
  AnalyticsTone,
} from "@/types";

/** A status tone's pill classes. Tones are the five defined in DESIGN.md. */
const pill = (tone: string) =>
  `border-status-${tone}-border bg-status-${tone}-bg text-status-${tone}-fg`;

// Written out rather than interpolated at the call site so Tailwind's scanner
// sees every class it has to generate.
const TONE_CLASS: Record<AnalyticsTone, string> = {
  positive: pill("success"),
  negative: pill("danger"),
  neutral: pill("neutral"),
  mixed: pill("warning"),
};

const TONE_LABEL: Record<AnalyticsTone, string> = {
  positive: "Positive",
  negative: "Negative",
  neutral: "Neutral",
  mixed: "Mixed",
};

const CONSENSUS_CLASS: Record<AnalyticsConsensus, string> = {
  stated: pill("success"),
  assumed: pill("warning"),
  none: pill("danger"),
};

// The wording matters more than the colour here. "Assumed" must not read as a
// weaker kind of agreement: nobody agreed, they just did not object.
const CONSENSUS_LABEL: Record<AnalyticsConsensus, string> = {
  stated: "Agreement stated",
  assumed: "Unchallenged",
  none: "Not resolved",
};

export const toneClass = (tone: AnalyticsTone): string =>
  TONE_CLASS[tone] ?? TONE_CLASS.neutral;

export const toneLabel = (tone: AnalyticsTone): string =>
  TONE_LABEL[tone] ?? TONE_LABEL.neutral;

export const consensusClass = (consensus: AnalyticsConsensus): string =>
  CONSENSUS_CLASS[consensus] ?? CONSENSUS_CLASS.assumed;

export const consensusLabel = (consensus: AnalyticsConsensus): string =>
  CONSENSUS_LABEL[consensus] ?? CONSENSUS_LABEL.assumed;

export interface SpeakerLookup {
  name: (speakerKey: string | null) => string;
  index: (speakerKey: string | null) => number;
}

/** Resolve stored speaker keys to the names and colours already on screen.
 *
 * The payload stores keys rather than names precisely so renaming a speaker
 * updates a stored analysis instead of orphaning it.
 */
export const buildSpeakerLookup = (
  speakers: AnalyticsSpeaker[],
): SpeakerLookup => {
  const positions = new Map<string, number>();
  speakers.forEach((speaker, position) =>
    positions.set(speaker.speaker_key, position),
  );
  return {
    name: (speakerKey) => {
      if (!speakerKey) return "Unattributed";
      const position = positions.get(speakerKey);
      return position === undefined
        ? "Unattributed"
        : speakers[position]!.name;
    },
    index: (speakerKey) =>
      speakerKey ? (positions.get(speakerKey) ?? 0) : 0,
  };
};
