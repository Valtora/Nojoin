/** How a save of a meeting's notes ended. */
export type NotesSaveOutcome =
  | { status: "saved"; revision?: string }
  | { status: "conflict"; resolution: Promise<NotesConflictChoice> }
  | { status: "failed" };

/**
 * What the user chose after a conflict: the other person's version, or their
 * own saved over it.
 */
export type NotesConflictChoice =
  | { choice: "theirs" }
  | { choice: "mine"; outcome: NotesSaveOutcome };
