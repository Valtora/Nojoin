import type {
  AnalyticsDelivery,
  AnalyticsDeliveryBaseline,
  AnalyticsDeliverySpeaker,
} from "@/types";

/** Plain-language readings of the measured delivery figures.
 *
 * A row of raw numbers asks every user to know what 197 wpm or 6.92 semitones
 * means, and most do not. These add a short descriptor beside each figure.
 *
 * Two rules keep that honest. The descriptors are **descriptive, never
 * evaluative**: "fast" is a property of speech, "rushed" would be a judgement
 * about a person, and nothing here is allowed to become the latter. And a
 * figure is only compared where the comparison means something -- pace has
 * corpus anchors, whereas pitch movement and pause frequency do not, so those
 * are compared with the other people in the same meeting and, where a person
 * has enough measured history, with their own usual figures.
 *
 * Pitch *height* is deliberately left uninterpreted. A descriptor there would
 * describe someone's voice rather than how they used it, which is a
 * characteristic of the person and outside what this surface reports.
 */

// Pace bands for the measure Nojoin computes: a median over per-utterance
// rates on utterances of 1.5s and up, which mostly excludes silent pauses and
// therefore runs above "speaking rate" folk numbers. Each edge is pinned to a
// corpus statistic (evidence in docs/ANALYTICS_EVIDENCE.md): 120 sits below
// the slowest whole conversation in Switchboard (111 wpm); 160 brackets
// audiobook narration (~155) and radio monologue (150-170); 160-200 spans
// turn-wise Switchboard (164) through Fisher (193); 200-240 covers CallHome
// (214) up to the silence-excluded Switchboard mean (236); past 240 is
// genuinely fast articulation, not merely few pauses. The bands are an
// English-language calibration -- speaking rates in words are not comparable
// across languages -- and the panel's footnote says so.
const PACE_DELIBERATE_MAX = 120;
const PACE_MEASURED_MAX = 160;
const PACE_CONVERSATIONAL_MAX = 200;
const PACE_BRISK_MAX = 240;

// How far from the meeting's own median counts as worth remarking on. Pitch
// spread is a stable measurement, so a fifth is a real difference; pause counts
// are noisier and need a wider band before a difference means anything. These
// remain judgement calls, disclosed as such.
const PITCH_DEVIATION = 0.2;
const PAUSE_DEVIATION = 0.3;

// Deviation from a person's own cross-meeting baseline that counts as worth
// remarking on. Pace varies within a speaker with utterance length and topic,
// so it gets the wider band. Judgement calls, disclosed as such.
const BASELINE_PACE_DEVIATION = 0.15;
const BASELINE_PITCH_DEVIATION = 0.2;
const BASELINE_PAUSE_DEVIATION = 0.3;

// Below this a speaker's figures rest on too little speech to compare with
// anyone else's. The absolute pace band still applies; the comparisons do not.
const MIN_UTTERANCES_FOR_COMPARISON = 5;

export interface DeliveryReading {
  pace: string | null;
  pitchMovement: string | null;
  pauses: string | null;
  /** Pauses per minute of this speaker's own speech, or null if unknown. */
  pauseRate: number | null;
  /** How this meeting compares with the person's own measured history. */
  baseline: string | null;
}

const median = (values: number[]): number | null => {
  if (!values.length) return null;
  const sorted = [...values].sort((a, b) => a - b);
  const middle = Math.floor(sorted.length / 2);
  return sorted.length % 2
    ? sorted[middle]!
    : (sorted[middle - 1]! + sorted[middle]!) / 2;
};

const paceBand = (wpm: number | null): string | null => {
  if (wpm === null || wpm <= 0) return null;
  if (wpm < PACE_DELIBERATE_MAX) return "deliberate";
  if (wpm < PACE_MEASURED_MAX) return "measured";
  if (wpm < PACE_CONVERSATIONAL_MAX) return "conversational";
  if (wpm < PACE_BRISK_MAX) return "brisk";
  return "fast";
};

const comparedWith = (
  value: number | null,
  reference: number | null,
  deviation: number,
  higher: string,
  lower: string,
): string | null => {
  if (value === null || reference === null || reference <= 0) return null;
  const ratio = value / reference;
  if (ratio >= 1 + deviation) return higher;
  if (ratio <= 1 - deviation) return lower;
  return null;
};

const pauseRateOf = (
  figures: AnalyticsDeliverySpeaker,
  speechMs: number | undefined,
): number | null => {
  if (!speechMs || speechMs <= 0) return null;
  return figures.pause_count / (speechMs / 60_000);
};

/** One remark against the person's own history, or null when unremarkable.
 *
 * At most one remark, in a fixed priority order, so the panel never stacks
 * three baseline clauses onto one row. Wording stays descriptive and names
 * the evidence: "across N meetings" is what separates a habit from a sample.
 */
const baselineReading = (
  figures: AnalyticsDeliverySpeaker,
  pauseRate: number | null,
  baseline: AnalyticsDeliveryBaseline | undefined,
): string | null => {
  if (!baseline) return null;
  const across = `their usual across ${baseline.meetings} meetings`;
  const pace = comparedWith(
    figures.words_per_minute,
    baseline.words_per_minute,
    BASELINE_PACE_DEVIATION,
    `faster than ${across}`,
    `slower than ${across}`,
  );
  if (pace) return pace;
  const pitch = comparedWith(
    figures.pitch_spread_semitones,
    baseline.pitch_spread_semitones,
    BASELINE_PITCH_DEVIATION,
    `more varied than ${across}`,
    `flatter than ${across}`,
  );
  if (pitch) return pitch;
  return comparedWith(
    pauseRate,
    baseline.pauses_per_minute,
    BASELINE_PAUSE_DEVIATION,
    `pauses more than ${across}`,
    `pauses less than ${across}`,
  );
};

/** Build one reading per measured speaker.
 *
 * `talkTimeMs` carries each speaker's speaking time so a pause *count* becomes
 * a pause *rate*. Without it the raw count says more about how long somebody
 * talked than about how they talked, which is why the panel shows the rate.
 */
export const buildDeliveryReadings = (
  delivery: AnalyticsDelivery,
  talkTimeMs: Record<string, number>,
  baselines: Record<string, AnalyticsDeliveryBaseline> = {},
): Record<string, DeliveryReading> => {
  const entries = Object.entries(delivery.speakers);
  const comparable = entries.filter(
    ([, figures]) => figures.analysed_utterances >= MIN_UTTERANCES_FOR_COMPARISON,
  );

  const medianPitch = median(
    comparable
      .map(([, figures]) => figures.pitch_spread_semitones)
      .filter((value): value is number => value !== null && value > 0),
  );
  const medianPauseRate = median(
    comparable
      .map(([key, figures]) => pauseRateOf(figures, talkTimeMs[key]))
      .filter((value): value is number => value !== null && value > 0),
  );

  const readings: Record<string, DeliveryReading> = {};
  for (const [key, figures] of entries) {
    const rate = pauseRateOf(figures, talkTimeMs[key]);
    const enoughSpeech =
      figures.analysed_utterances >= MIN_UTTERANCES_FOR_COMPARISON;
    readings[key] = {
      pace: paceBand(figures.words_per_minute),
      // Only ever relative: the same expressive range measures differently on
      // different voices, so an absolute band would compare vocal anatomy.
      pitchMovement: enoughSpeech
        ? comparedWith(
            figures.pitch_spread_semitones,
            medianPitch,
            PITCH_DEVIATION,
            "more varied than others here",
            "flatter than others here",
          )
        : null,
      pauses: enoughSpeech
        ? comparedWith(
            rate,
            medianPauseRate,
            PAUSE_DEVIATION,
            "pauses more than others here",
            "pauses less than others here",
          )
        : null,
      pauseRate: rate,
      baseline: enoughSpeech
        ? baselineReading(figures, rate, baselines[key])
        : null,
    };
  }
  return readings;
};
