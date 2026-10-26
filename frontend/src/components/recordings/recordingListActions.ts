import {
  Archive,
  ArchiveRestore,
  RotateCcw,
  Shredder,
  Tag,
  Trash2,
  type LucideIcon,
} from "lucide-react";

import type { BatchRecordingResult } from "@/lib/api";
import type { ViewType } from "@/lib/store";

/**
 * The recordings list's action table: what each view offers on a selection,
 * and the words every surface uses for those actions. The batch bar shows a
 * view's actions in this order, and the row menu ends with the same ones,
 * Tag aside, so one row and many behave alike.
 */
export type ListActionId =
  | "tag"
  | "archive"
  | "unarchive"
  | "softDelete"
  | "restore"
  | "permanentDelete";

interface ListAction {
  label: string;
  icon: LucideIcon;
  /** Destroys something for good, so it is drawn as danger and confirmed. */
  destructive?: boolean;
}

export const LIST_ACTIONS: Record<ListActionId, ListAction> = {
  tag: { label: "Tag", icon: Tag },
  archive: { label: "Archive", icon: Archive },
  unarchive: { label: "Unarchive", icon: ArchiveRestore },
  softDelete: { label: "Move to Bin", icon: Trash2 },
  restore: { label: "Restore", icon: RotateCcw },
  permanentDelete: { label: "Delete Permanently", icon: Shredder, destructive: true },
};

export const VIEW_ACTIONS: Record<ViewType, readonly ListActionId[]> = {
  recordings: ["tag", "archive", "softDelete"],
  archived: ["tag", "unarchive", "softDelete"],
  deleted: ["restore", "permanentDelete"],
};

/** How long a notification offering Undo stays, so there is time to reach the button. */
export const UNDO_NOTIFICATION_MS = 10000;

/** "1 recording", "3 recordings". */
export const recordingsPhrase = (count: number): string =>
  `${count} ${count === 1 ? "recording" : "recordings"}`;

const theRecordings = (count: number): string =>
  count === 1 ? "the recording" : "the recordings";

export interface ResultText {
  /** The sentence for what was done, given "3 recordings" or "2 of 3 recordings". */
  done: (phrase: string) => string;
  /** The sentence when the server changed nothing, given how many were asked for. */
  nothing: (requested: number) => string;
  /** Said after `done` when some were left as they were, given how many. */
  skippedNote?: (skipped: number) => string;
  failed: (requested: number) => string;
}

export const RESULT_TEXT: Record<Exclude<ListActionId, "tag">, ResultText> = {
  archive: {
    done: (phrase) => `Archived ${phrase}.`,
    nothing: () => "Nothing was archived.",
    failed: (n) => `Failed to archive ${theRecordings(n)}.`,
  },
  unarchive: {
    done: (phrase) => `Unarchived ${phrase}.`,
    nothing: () => "Nothing was unarchived.",
    failed: (n) => `Failed to unarchive ${theRecordings(n)}.`,
  },
  softDelete: {
    done: (phrase) => `Moved ${phrase} to the bin.`,
    nothing: () => "Nothing was moved to the bin.",
    failed: (n) => `Failed to move ${theRecordings(n)} to the bin.`,
  },
  restore: {
    done: (phrase) => `Restored ${phrase}.`,
    nothing: () => "Nothing was restored.",
    failed: (n) => `Failed to restore ${theRecordings(n)}.`,
  },
  permanentDelete: {
    done: (phrase) => `Deleted ${phrase} permanently.`,
    nothing: (n) =>
      n === 1
        ? "Nothing was deleted: the recording was no longer in the bin."
        : "Nothing was deleted: the recordings were no longer in the bin.",
    skippedNote: (k) =>
      k === 1
        ? "1 was no longer in the bin, so it was kept."
        : `${k} were no longer in the bin, so they were kept.`,
    failed: (n) => `Failed to delete ${theRecordings(n)} permanently.`,
  },
};

export const tagResultText = (tagName: string): ResultText => ({
  done: (phrase) => `Added "${tagName}" to ${phrase}.`,
  nothing: (n) =>
    n === 1
      ? `The recording already had "${tagName}".`
      : `The recordings already had "${tagName}".`,
  skippedNote: (k) =>
    k === 1 ? "The other already had it." : "The others already had it.",
  failed: (n) => `Failed to tag ${theRecordings(n)}.`,
});

/** The notification for a batch result, counted by the server. */
export function describeResult(
  text: ResultText,
  result: BatchRecordingResult,
): { type: "success" | "warning"; message: string } {
  const requested = result.count + result.skipped;
  if (result.count === 0) {
    return { type: "warning", message: text.nothing(requested) };
  }
  if (result.skipped === 0) {
    return { type: "success", message: text.done(recordingsPhrase(result.count)) };
  }
  const done = text.done(`${result.count} of ${recordingsPhrase(requested)}`);
  const note = text.skippedNote?.(result.skipped);
  return { type: "success", message: note ? `${done} ${note}` : done };
}

/** Where an undo puts recordings back. */
export type UndoDestination = "Recordings" | "Archived" | "the bin";

export const movedBackText = (count: number, destination: UndoDestination): string =>
  `Moved ${recordingsPhrase(count)} back to ${destination}.`;

export const untaggedText = (count: number, tagName: string): string =>
  `Removed "${tagName}" from ${recordingsPhrase(count)}.`;

export const NOTHING_TO_UNDO = "Nothing was left to undo.";
export const UNDO_FAILED = "Failed to undo.";

/** The permanent delete confirmation, the one list action that asks first. */
export const permanentDeleteConfirmation = (count: number) => ({
  title:
    count === 1
      ? "Delete this recording permanently?"
      : `Delete ${count} recordings permanently?`,
  message:
    count === 1
      ? "This removes the recording, its audio and its attached documents for good. It cannot be undone."
      : "This removes the recordings, their audio and their attached documents for good. It cannot be undone.",
  confirmText: LIST_ACTIONS.permanentDelete.label,
});
