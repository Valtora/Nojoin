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
  /** Puts the note box's next note at a time, quoting nothing; without it a settling line takes no note. */
  onNoteAtTime?: (atMs: number) => void;
}

/**
 * The live view's ⋯ menu on a line. Every line can take the note box's next
 * note: a locked line's quotes it, and a line still settling places it at the
 * line's start, as a typed note. Lines of a recording using word settling also
 * take corrections, speaker assignment and word splits, which wait for the
 * line to lock.
 */
export default function LiveLineMenu({ segment, onNoteOnLine, onNoteAtTime, handlers, speakers }: LiveLineMenuProps) {
  const [editing, setEditing] = useState<{
    kind: LiveEditKind; anchor: HTMLElement; segment: TranscriptSegment;
  } | null>(null);
  const selection = lineSelection(segment);
  const canEdit = !!handlers && segment.settling_required === true && !!segment.id;
  const settling = lineIsSettling(segment);
  const items = lineActionsFor({
    view: "live",
    canNote: settling ? onNoteAtTime !== undefined : selection !== null,
    canEdit,
    provisional: true,
    canPlay: false,
    settlingRequired: segment.settling_required,
    disabledReason: settling ? LINE_SETTLING_REASON : undefined,
    canSplit: canEdit && splitWords(segment).length > 0,
  }).map((action) => ({
    ...action,
    onSelect: (anchor: HTMLButtonElement) => {
      if (action.id === "note") {
        if (settling) onNoteAtTime?.(segmentStartMs(segment));
        else if (selection) onNoteOnLine(selection);
        return;
      }
      if (settling) return;
      if (action.id === "editText" || action.id === "changeSpeaker" || action.id === "split") {
        setEditing({ kind: action.id, anchor, segment });
      }
    },
  }));

  return (
    <>
    <TranscriptActionsMenu
      label={`Actions for the line at ${formatTimelineMs(segmentStartMs(segment))}`}
      items={items}
      triggerClassName="-my-0.5 h-6 w-6 shrink-0 pointer-coarse:-my-2.5"
    />
    {editing && handlers && !settling ? <LiveLineEditor
      key={`${editing.kind}-${editing.segment.id}`} kind={editing.kind} segment={editing.segment} anchor={editing.anchor}
      handlers={handlers} speakers={speakers} onClose={() => setEditing(null)}
    /> : null}
    </>
  );
}
