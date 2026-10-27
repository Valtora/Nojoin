"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import { noteTargetForLine } from "@/lib/notes/lineSelection";
import { isNoteSelection, type NoteTarget, type OutboxDraft } from "@/lib/notes/noteOutbox";
import { lineActionsFor, type LineActionId } from "@/lib/transcript/actions";
import type { TranscriptSegment } from "@/types";
import { lineIsSettling, LINE_SETTLING_REASON } from "@/lib/transcript/settling";

import NoteOnSelectionPopover from "../notes/NoteOnSelectionPopover";

import type { TranscriptLineActions } from "./_hooks/useTranscriptEditing";
import TranscriptActionsMenu from "./TranscriptActionsMenu";
import { formatTime } from "./transcriptDisplay";

interface TranscriptLineMenuProps {
  segment: TranscriptSegment;
  segmentId: string;
  speakerName: string;
  /** The line's text and speaker can't be changed. */
  isSegmentReadOnly: boolean;
  actions: TranscriptLineActions;
  /** Queues a note; null where notes can't be added. */
  onAddNote: ((note: OutboxDraft) => string | null) | null;
  /** Plays on from a time in seconds; null where playback is off. */
  onPlayFrom: ((startSeconds: number) => void) | null;
  isEditingText: boolean;
  /** The line's speaker popover is open. */
  isChangingSpeaker: boolean;
}

/**
 * The finished view's ⋯ menu on a line: a note on the line, and the line's
 * own gestures (edit the text, change the speaker, play) from the keyboard.
 * What an item opens sits against the ⋯ button, and focus comes back to it
 * when that closes.
 */
export default function TranscriptLineMenu({
  segment,
  segmentId,
  speakerName,
  isSegmentReadOnly,
  actions,
  onAddNote,
  onPlayFrom,
  isEditingText,
  isChangingSpeaker,
}: TranscriptLineMenuProps) {
  const [noteAnchor, setNoteAnchor] = useState<HTMLElement | null>(null);
  const [noteTarget, setNoteTarget] = useState<NoteTarget | null>(null);
  /** The ⋯ button, while an editor or popover an item opened is open. */
  const returnFocusTo = useRef<HTMLElement | null>(null);
  const selection = useMemo(() => noteTargetForLine(segment), [segment]);
  const activeNoteTarget = noteTarget && !isNoteSelection(noteTarget) ? noteTarget : selection;

  // Back to the ⋯ button once what an item opened has closed, unless focus
  // went somewhere on purpose (a click elsewhere).
  const opened = isEditingText || isChangingSpeaker;
  useEffect(() => {
    if (opened || !returnFocusTo.current) return;
    const trigger = returnFocusTo.current;
    returnFocusTo.current = null;
    if (document.activeElement === document.body || document.activeElement === null) {
      trigger.focus();
    }
  }, [opened]);

  const run: Record<LineActionId, (trigger: HTMLButtonElement) => void> = {
    note: (trigger) => { setNoteTarget(selection); setNoteAnchor(trigger); },
    editText: (trigger) => {
      returnFocusTo.current = trigger;
      actions.startTextEdit(segment, segmentId);
    },
    changeSpeaker: (trigger) => {
      returnFocusTo.current = trigger;
      actions.openSpeakerPopover(segmentId, trigger);
    },
    play: () => onPlayFrom?.(segment.start),
    split: () => {},
  };

  const items = lineActionsFor({
    view: "finished",
    canNote: onAddNote !== null && selection !== null,
    canEdit: !isSegmentReadOnly || lineIsSettling(segment),
    provisional: segment.provisional === true,
    canPlay: onPlayFrom !== null,
    settlingRequired: segment.settling_required,
    disabledReason: lineIsSettling(segment) ? LINE_SETTLING_REASON : undefined,
  }).map((action) => ({ ...action, onSelect: run[action.id] }));

  return (
    <>
      <TranscriptActionsMenu
        label={`Actions for ${speakerName}'s line at ${formatTime(segment.start)}`}
        items={items}
        active={noteAnchor !== null || opened}
        triggerClassName="ml-auto self-center -my-1 pointer-coarse:-my-2.5"
      />
      {noteAnchor && activeNoteTarget && onAddNote ? (
        <NoteOnSelectionPopover
          quote={isNoteSelection(activeNoteTarget) ? activeNoteTarget.quotedText : null}
          title={`Note on the line at ${formatTime(isNoteSelection(activeNoteTarget)
            ? segment.start : activeNoteTarget.startMs / 1000)}`}
          anchor={noteAnchor}
          onSubmit={(body) => onAddNote(isNoteSelection(activeNoteTarget)
            ? { kind: "selection", body, anchorMs: null, selection: activeNoteTarget }
            : { kind: "point", body, anchorMs: activeNoteTarget.startMs, span: activeNoteTarget })}
          onClose={() => setNoteAnchor(null)}
        />
      ) : null}
    </>
  );
}
