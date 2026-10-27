import type { TranscriptSegment, TranscriptSourceReference } from "@/types";

/** Live names carry source identity even when optional word settling is disabled. */
export function liveSpeakerSourceReference(segment: TranscriptSegment): TranscriptSourceReference | null {
  const revision = segment.revision;
  return segment.id && revision !== undefined && Number.isInteger(revision) && revision > 0
    ? { id: segment.id, revision }
    : null;
}
