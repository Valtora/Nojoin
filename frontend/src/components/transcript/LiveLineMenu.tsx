"use client";

import { lineSelection } from "@/lib/notes/lineSelection";
import type { NoteSelection } from "@/lib/notes/noteOutbox";
import { formatTimelineMs, segmentStartMs } from "@/lib/notes/placement";
import { lineActionsFor } from "@/lib/transcript/actions";
import { lineIsSettling, LINE_SETTLING_REASON, splitWords } from "@/lib/transcript/settling";
import type { RecordingSpeaker, TranscriptSegment } from "@/types";
import { useState } from "react";
import LiveLineEditor, { type LiveEditKind, type LiveLineHandlers } from "./LiveLineEditor";

import TranscriptActionsMenu from "./TranscriptActionsMenu";

interface LiveLineMenuProps {
  handlers?: LiveLineHandlers;
  speakers?: readonly RecordingSpeaker[];
  segment: TranscriptSegment;
  /** Puts the note box's next note on the line. */
  onNoteOnLine: (selection: NoteSelection) => void;
}

/**
 * The live view's ⋯ menu on a line. Every line can take the note box's next
 * note, quoting the line once its words lock. Lines of a recording using word
 * settling also take corrections, speaker assignment and word splits, open or
 * locked: a change on an open line freezes it against later decodes.
 */
export default function LiveLineMenu({ segment, onNoteOnLine, handlers, speakers }: LiveLineMenuProps) {
  const [editing, setEditing] = useState<{
    kind: LiveEditKind; anchor: HTMLElement; segment: TranscriptSegment;
  } | null>(null);
  const selection = lineSelection(segment);
  const canEdit = !!handlers && segment.settling_required === true && !!segment.id;
  const settling = lineIsSettling(segment);
  const items = lineActionsFor({
    view: "live",
    canNote: selection !== null,
    canEdit,
    provisional: true,
    canPlay: false,
    settlingRequired: segment.settling_required,
    noteDisabledReason: settling ? LINE_SETTLING_REASON : undefined,
    canSplit: canEdit && splitWords(segment).length > 0,
  }).map((action) => ({
    ...action,
    onSelect: (anchor: HTMLButtonElement) => {
      if (action.id === "note") {
        if (selection) onNoteOnLine(selection);
        return;
      }
      if (action.id === "editText" || action.id === "changeSpeaker" || action.id === "split") {
        setEditing({ kind: action.id, anchor, segment });
      }
    },
  }));
  // A save is sent against the line as the editor opened it. When it fails,
  // most often because a settling pass changed the line meanwhile, the editor
  // moves to the line as it is now and keeps what was typed: Save again
  // applies it there.
  const rebase = (saved: boolean) => {
    if (!saved) setEditing((open) => open && { ...open, segment });
    return saved;
  };
  const editorHandlers: LiveLineHandlers | undefined = handlers && {
    editText: async (line, text) => rebase(await handlers.editText(line, text)),
    changeSpeaker: async (line, assignment) => rebase(await handlers.changeSpeaker(line, assignment)),
    split: async (line, wordId) => rebase(await handlers.split(line, wordId)),
  };

  return (
    <>
    <TranscriptActionsMenu
      label={`Actions for the line at ${formatTimelineMs(segmentStartMs(segment))}`}
      items={items}
      triggerClassName="-my-0.5 h-6 w-6 shrink-0 pointer-coarse:-my-2.5"
    />
    {editing && editorHandlers ? <LiveLineEditor
      key={`${editing.kind}-${editing.segment.id}`} kind={editing.kind} segment={editing.segment} anchor={editing.anchor}
      handlers={editorHandlers} speakers={speakers} onClose={() => { setEditing(null); editing.anchor.focus(); }}
    /> : null}
    </>
  );
}
