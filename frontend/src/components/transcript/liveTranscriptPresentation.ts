import { liveSpeakerDisplayName } from "@/lib/recordingSpeakerUtils";
import { getTranscriptSegmentKey } from "@/lib/transcriptSegments";
import { lineIsSettling } from "@/lib/transcript/settling";
import type { NoteTarget } from "@/lib/notes/noteOutbox";
import type { TranscriptSegment } from "@/types";
import type { LiveLineHandlers } from "./LiveLineEditor";
import type { IndexedTranscriptSegment } from "./transcriptGroups";

export type LiveInteractionChange = (
  identity: string, active: boolean, snapshot?: IndexedTranscriptSegment,
) => void;

export const liveRenderIdentity = (segment: TranscriptSegment, index: number) =>
  segment.id ?? `${segment.start}-${index}`;

export interface LiveTranscriptPresentation {
  hasLoaded: boolean;
  isPaused?: boolean;
  lineHandlers?: LiveLineHandlers;
  onNoteOnLine?: (target: NoteTarget) => void;
  onRenameLiveSpeaker?: (
    label: string, name: string, segment?: TranscriptSegment,
  ) => Promise<void | boolean>;
  loadPeopleNames?: () => Promise<readonly string[]>;
}

/** Live headers follow adjacent speaker runs, independently of playback groups. */
export interface LiveLinePresentation {
  sourceLabel: string | null;
  speakerLabel: string | null;
  sameSpeakerContinuation: boolean;
  speakerLocked: boolean;
  namingAnchor?: TranscriptSegment;
}

const sourceChannelOf = (segment: TranscriptSegment) =>
  segment.source_channel === "microphone" || segment.source_channel === "system"
    ? segment.source_channel : null;

const liveLabelOf = (segment: TranscriptSegment) =>
  liveSpeakerDisplayName(segment.speaker) === null ? null : segment.speaker;

const speakerLabelOf = (segment: TranscriptSegment) => liveLabelOf(segment) ??
  (segment.settling_required && segment.speaker !== "UNKNOWN" ? segment.speaker : null);

const namingAnchorForRun = (segments: readonly TranscriptSegment[], index: number) => {
  const first = segments[index];
  if (!lineIsSettling(first)) return first;
  for (let next = index + 1; next < segments.length; next += 1) {
    const candidate = segments[next];
    if (candidate.settling_required !== true || candidate.speaker !== first.speaker) break;
    if (candidate.locked === true) return candidate;
  }
  return undefined;
};

export function buildLiveTranscriptPresentation(
  segments: readonly TranscriptSegment[],
): ReadonlyMap<string, LiveLinePresentation> {
  return new Map(segments.map((segment, index) => {
    const previous = index > 0 ? segments[index - 1] : undefined;
    const channel = sourceChannelOf(segment);
    const label = speakerLabelOf(segment);
    const previousLabel = previous
      ? segment.settling_required ? speakerLabelOf(previous) : liveLabelOf(previous)
      : null;
    const showSpeaker = label !== null && (!previous || label !== previousLabel);
    return [getTranscriptSegmentKey(segment, index), {
      sourceLabel: channel !== null && channel !== (previous ? sourceChannelOf(previous) : null)
        ? channel === "microphone" ? "Microphone" : "Shared audio" : null,
      speakerLabel: showSpeaker ? label : null,
      sameSpeakerContinuation: segment.settling_required === true &&
        previous !== undefined && segment.speaker === previous.speaker,
      speakerLocked: segment.settling_required ? segment.locked === true : segment.speaker_state === "stable",
      namingAnchor: showSpeaker ? namingAnchorForRun(segments, index) : undefined,
    }];
  }));
}
