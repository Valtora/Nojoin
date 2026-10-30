import { liveSpeakerDisplayName } from "@/lib/recordingSpeakerUtils";
import { getTranscriptSegmentKey } from "@/lib/transcriptSegments";
import type { NoteSelection } from "@/lib/notes/noteOutbox";
import type { TranscriptSegment } from "@/types";
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
}

const sourceChannelOf = (segment: TranscriptSegment) =>
  segment.source_channel === "microphone" || segment.source_channel === "system"
    ? segment.source_channel : null;

const liveLabelOf = (segment: TranscriptSegment) =>
  liveSpeakerDisplayName(segment.speaker) === null ? null : segment.speaker;

const speakerLabelOf = (segment: TranscriptSegment) => liveLabelOf(segment) ??
  (segment.settling_required && segment.speaker !== "UNKNOWN" ? segment.speaker : null);

export function buildLiveTranscriptPresentation(
  segments: readonly TranscriptSegment[],
): ReadonlyMap<string, LiveLinePresentation> {
  return new Map(segments.map((segment, index) => {
    const previous = index > 0 ? segments[index - 1] : undefined;
    const channel = sourceChannelOf(segment);
    return [getTranscriptSegmentKey(segment, index), {
      sourceLabel: channel !== null && channel !== (previous ? sourceChannelOf(previous) : null)
        ? channel === "microphone" ? "Microphone" : "Shared audio" : null,
      speakerLabel: speakerLabelOf(segment),
      speakerLocked: segment.settling_required ? segment.locked === true : segment.speaker_state === "stable",
    }];
  }));
}
