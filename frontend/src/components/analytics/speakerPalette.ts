import { normalisePaletteColorKey } from "@/lib/recordingSpeakerUtils";
import type { AnalyticsSpeaker } from "@/types";

// Colour identifies a person on this surface, and a person has exactly one
// colour in a meeting. That colour is chosen by buildMeetingSpeakerColors, is
// changeable by the user, and is what the transcript, the speaker panel and
// the assignment popover already draw. Analytics resolves it to the matching
// --speaker-* token so a chart fill can use the same value as a Tailwind dot
// elsewhere in the same view.
//
// Two rules the design depends on, both easy to break by accident:
//
// 1. A colour follows the speaker, never their position in a list. Analytics
//    orders speakers by talking time and the speaker panel orders them
//    alphabetically, so anything keyed on index disagrees with the rest of the
//    meeting view by construction -- which is exactly what the --chart-* slots
//    used to do here.
// 2. Past the last fallback slot, speakers fold into one neutral "other"
//    colour rather than wrapping back to slot 1. A repeated hue reads as the
//    same person, which is worse than an honest "everyone else".
export const CHART_SLOT_COUNT = 8;

/** The fallback for a speaker with no assigned colour, by list position. */
export const slotColor = (index: number): string =>
  index < CHART_SLOT_COUNT
    ? `var(--chart-${index + 1})`
    : "var(--chart-other)";

/**
 * Resolve every speaker to the colour they already wear in this meeting.
 *
 * `assigned` is the recording view's own speaker-colour map, keyed by every
 * alias a speaker answers to (diarisation label, name, global name). It is
 * computed client-side from the transcript, so it is the only place the
 * auto-assigned colours exist -- the payload's `color` field carries a colour
 * only once the user has explicitly chosen one. Both are consulted, in that
 * order, and a speaker matching neither keeps the old slot colour rather than
 * going uncoloured.
 */
export const buildSpeakerColors = (
  speakers: AnalyticsSpeaker[],
  assigned: Record<string, string> = {},
): Record<string, string> => {
  const colors: Record<string, string> = {};
  speakers.forEach((speaker, index) => {
    const key =
      normalisePaletteColorKey(
        speaker.diarization_label
          ? assigned[speaker.diarization_label]
          : undefined,
      ) ??
      normalisePaletteColorKey(assigned[speaker.name]) ??
      normalisePaletteColorKey(speaker.color);
    colors[speaker.speaker_key] = key
      ? `var(--speaker-${key})`
      : slotColor(index);
  });
  return colors;
};
