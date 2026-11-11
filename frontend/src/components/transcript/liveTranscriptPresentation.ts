import { liveSpeakerDisplayName } from "@/lib/recordingSpeakerUtils";
import { getTranscriptSegmentKey } from "@/lib/transcriptSegments";
import type { NoteSelection } from "@/lib/notes/noteOutbox";
import type { SpeakerNameSource, TranscriptSegment } from "@/types";
import type { LiveLineHandlers } from "./LiveLineEditor";

export const liveRenderIdentity = (segment: TranscriptSegment, index: number) =>
  segment.id ?? `${segment.start}-${index}`;

/**
 * A mark that moves with each new live line and each change to the last one,
 * which the meter's quiet notice takes as proof that audio is getting through.
 * The last line's text length is in it because settling revises a line's
 * words in place without moving its end.
 */
export function liveTextActivityMark(segments: readonly TranscriptSegment[]): string | null {
  const last = segments.at(-1);
  return last ? `${segments.length}:${last.end}:${last.text.length}` : null;
}

export interface LiveTranscriptPresentation {
  hasLoaded: boolean;
  /**
   * The capture is recording or paused, so a view with no lines yet is
   * listening. Once it stops, the feed shows what it heard while it processes.
   */
  capturing?: boolean;
  isPaused?: boolean;
  /** The last live read said live transcription is behind the uploads. */
  liveBehind?: boolean;
  lineHandlers?: LiveLineHandlers;
  onNoteOnLine?: (selection: NoteSelection) => void;
  /** Puts the note box's next note at a line's start: the note from a line still settling. */
  onNoteAtTime?: (atMs: number) => void;
  onRenameLiveSpeaker?: (label: string, name: string) => Promise<boolean>;
  /** Merges live speaker `label` into `intoLabel`, while the capture is open. */
  onMergeLiveSpeaker?: (label: string, intoLabel: string) => Promise<boolean>;
  liveSpeakerSuggestions?: Readonly<Record<string, string>>;
  liveSpeakerSuggestionSources?: Readonly<Record<string, SpeakerNameSource>>;
  /**
   * Where each pending name was heard, in seconds. Read only to place the
   * name ({@link buildLiveTranscriptPresentation}), so a change redraws the
   * lines it moves between, not every line.
   */
  liveSpeakerSuggestionStarts?: Readonly<Record<string, number>>;
  onDismissLiveSpeakerSuggestion?: (label: string) => Promise<boolean>;
  loadPeopleNames?: () => Promise<readonly string[]>;
}

/**
 * Every labelled live line names its speaker, as a finished line does; the
 * source label shows only where the audio source changes.
 */
export interface LiveLinePresentation {
  sourceLabel: string | null;
  speakerLabel: string | null;
  speakerLocked: boolean;
  /**
   * The line that offers the speaker's pending name, if they have one: the
   * line that holds where the name was heard, else the speaker's last line.
   */
  offersSuggestion: boolean;
}

const sourceChannelOf = (segment: TranscriptSegment) =>
  segment.source_channel === "microphone" || segment.source_channel === "system"
    ? segment.source_channel : null;

const liveLabelOf = (segment: TranscriptSegment) =>
  liveSpeakerDisplayName(segment.speaker) === null ? null : segment.speaker;

const speakerLabelOf = (segment: TranscriptSegment) => liveLabelOf(segment) ??
  (segment.settling_required && segment.speaker !== "UNKNOWN" ? segment.speaker : null);

// Line and evidence times both come from the server in seconds; this only
// absorbs rounding.
const SAME_INSTANT_SECONDS = 0.001;

/**
 * The line that offers each pending name, by label. A name heard at a known
 * time (a self-introduction) sits on the speaker's line that holds that time,
 * the last such line where two touch at it. A name with no time (an AI name
 * without a single timed quote), or whose line is no longer the speaker's,
 * sits on the speaker's last line, where the reader is.
 */
function suggestionLines(
  segments: readonly TranscriptSegment[],
  suggestions: Readonly<Record<string, string>>,
  starts: Readonly<Record<string, number>>,
): Map<string, number> {
  const lines = new Map<string, number>();
  const anchored = new Set<string>();
  segments.forEach((segment, index) => {
    const label = speakerLabelOf(segment);
    if (label === null || !suggestions[label]) return;
    const at = starts[label];
    if (at === undefined) {
      lines.set(label, index);
      return;
    }
    const holds = segment.start <= at + SAME_INSTANT_SECONDS && at - SAME_INSTANT_SECONDS <= segment.end;
    if (holds) anchored.add(label);
    if (holds || !anchored.has(label)) lines.set(label, index);
  });
  return lines;
}

const NONE: Readonly<Record<string, never>> = {};

/**
 * How each live line is drawn. Only the lines that offer a pending name read
 * `suggestions`, so a new name changes one line's presentation, and a line a
 * speaker adds changes no other line's.
 */
export function buildLiveTranscriptPresentation(
  segments: readonly TranscriptSegment[],
  suggestions: Readonly<Record<string, string>> = NONE,
  suggestionStarts: Readonly<Record<string, number>> = NONE,
): ReadonlyMap<string, LiveLinePresentation> {
  const offering = suggestionLines(segments, suggestions, suggestionStarts);
  return new Map(segments.map((segment, index) => {
    const previous = index > 0 ? segments[index - 1] : undefined;
    const channel = sourceChannelOf(segment);
    const speakerLabel = speakerLabelOf(segment);
    return [getTranscriptSegmentKey(segment, index), {
      sourceLabel: channel !== null && channel !== (previous ? sourceChannelOf(previous) : null)
        ? channel === "microphone" ? "Microphone" : "Shared audio" : null,
      speakerLabel,
      speakerLocked: segment.settling_required ? segment.locked === true : segment.speaker_state === "stable",
      offersSuggestion: speakerLabel !== null && offering.get(speakerLabel) === index,
    }];
  }));
}
