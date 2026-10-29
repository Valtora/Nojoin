import { updateTranscriptUtteranceSpeaker, updateTranscriptUtteranceText } from "@/lib/api";
import { splitTranscriptUtterance } from "@/lib/api/transcriptSplit";
import { getErrorDetail, getErrorStatus } from "@/lib/errors";
import type { RecordingId, TranscriptSegment, TranscriptSpeakerAssignment } from "@/types";

/**
 * Each resolves true once the line's editor is done: the change landed, or the
 * line changed since it was shown and the notice says so. Either way the
 * caller reloads the transcript. False means the change was refused, and the
 * editor keeps what was typed.
 */
export interface TranscriptRecordingActions {
  editTranscriptLine: (id: RecordingId, segment: TranscriptSegment, text: string) => Promise<boolean>;
  assignTranscriptLine: (id: RecordingId, segment: TranscriptSegment, assignment: TranscriptSpeakerAssignment) => Promise<boolean>;
  splitTranscriptLine: (id: RecordingId, segment: TranscriptSegment, wordId: string) => Promise<boolean>;
}

/**
 * The API's answers to a command sent against a revision the line has since
 * left. Retrying one fails every time, so it ends the edit instead.
 */
const REVISION_CONFLICTS = new Set([
  "Utterance revision conflict",
  "This line changed. Reload it and try again.",
]);

export const LINE_CHANGED_MESSAGE =
  "This line changed while you were editing. Open it again to edit the latest text.";

const isRevisionConflict = (error: unknown) =>
  getErrorStatus(error) === 409 && REVISION_CONFLICTS.has(getErrorDetail(error) ?? "");

/** Shared command execution and failure reporting for line actions on a recording. */
export function transcriptLineActions(notify: (message: string) => void): TranscriptRecordingActions {
  const run = async (segment: TranscriptSegment, command: () => Promise<unknown>) => {
    if (!segment.id || segment.revision === undefined) return false;
    try {
      await command();
      return true;
    } catch (error) {
      if (isRevisionConflict(error)) {
        notify(LINE_CHANGED_MESSAGE);
        return true;
      }
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
