import type { Recording } from "@/types";

/** Whether the caller owns the meeting. Older servers send no access: theirs. */
export const ownsRecording = (recording: Pick<Recording, "access">): boolean =>
  (recording.access ?? "owner") === "owner";

/** Whether the caller may change the meeting's shared content. */
export const canEditRecording = (recording: Pick<Recording, "access">): boolean => {
  const access = recording.access ?? "owner";
  return access === "owner" || access === "edit";
};

/** How to name the owner of a shared meeting. */
export const ownerLabel = (recording: Pick<Recording, "owner">): string =>
  recording.owner?.display_name || recording.owner?.username || "someone";

/**
 * Whether the owner archived or binned a meeting shared with the caller: it is
 * then read-only for them, and they can ask the owner to bring it back.
 */
export const isPutAwayByOwner = (
  recording: Pick<Recording, "access" | "is_archived" | "is_deleted">,
): boolean => !ownsRecording(recording) && (recording.is_archived || recording.is_deleted);
