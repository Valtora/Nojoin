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
