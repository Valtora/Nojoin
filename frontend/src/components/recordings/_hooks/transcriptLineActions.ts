import { updateTranscriptUtteranceSpeaker, updateTranscriptUtteranceText } from "@/lib/api";
import { splitTranscriptUtterance } from "@/lib/api/transcriptSplit";
import { getErrorDetail } from "@/lib/errors";
import type { RecordingId, TranscriptSegment, TranscriptSpeakerAssignment } from "@/types";

export interface TranscriptRecordingActions {
  editTranscriptLine: (id: RecordingId, segment: TranscriptSegment, text: string) => Promise<boolean>;
  assignTranscriptLine: (id: RecordingId, segment: TranscriptSegment, assignment: TranscriptSpeakerAssignment) => Promise<boolean>;
  splitTranscriptLine: (id: RecordingId, segment: TranscriptSegment, wordId: string) => Promise<boolean>;
}

/** Shared command execution and failure reporting for line actions on a recording. */
export function transcriptLineActions(notify: (message: string) => void): TranscriptRecordingActions {
  const run = async (segment: TranscriptSegment, command: () => Promise<unknown>) => {
    if (!segment.id || segment.revision === undefined) return false;
    try {
      await command();
      return true;
    } catch (error) {
      notify(getErrorDetail(error) ?? "This line could not be changed. Try again.");
      return false;
    }
  };
  return {
    editTranscriptLine: (id, segment, text) => run(segment, () =>
      updateTranscriptUtteranceText(id, segment.id!, text, segment.revision)),
    assignTranscriptLine: (id, segment, assignment) => run(segment, () =>
      updateTranscriptUtteranceSpeaker(id, segment.id!, assignment, segment.revision)),
    splitTranscriptLine: (id, segment, wordId) => run(segment, () =>
      splitTranscriptUtterance(id, segment.id!, wordId, segment.revision!)),
  };
}
