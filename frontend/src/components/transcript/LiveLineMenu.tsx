"use client";

import { lineSelection } from "@/lib/notes/lineSelection";
import type { NoteSelection } from "@/lib/notes/noteOutbox";
import { formatTimelineMs, segmentStartMs } from "@/lib/notes/placement";
import { lineActionsFor } from "@/lib/transcript/actions";
import type { TranscriptSegment } from "@/types";

import TranscriptActionsMenu from "./TranscriptActionsMenu";

interface LiveLineMenuProps {
  segment: TranscriptSegment;
  /** Puts the note box's next note on the line. */
  onNoteOnLine: (selection: NoteSelection) => void;
}

/**
 * The live view's ⋯ menu on a line, whose one action puts the note box's
 * next note on the line. A line still without text or span has none.
 */
export default function LiveLineMenu({ segment, onNoteOnLine }: LiveLineMenuProps) {
  const selection = lineSelection(segment);
  const items = lineActionsFor({
    view: "live",
    canNote: selection !== null,
    canEdit: false,
    provisional: true,
    canPlay: false,
  }).map((action) => ({
    ...action,
    onSelect: () => {
      if (selection) onNoteOnLine(selection);
    },
  }));

  return (
    <TranscriptActionsMenu
      label={`Actions for the line at ${formatTimelineMs(segmentStartMs(segment))}`}
      items={items}
      triggerClassName="-my-0.5 h-6 w-6 shrink-0 pointer-coarse:-my-2.5"
    />
  );
}
