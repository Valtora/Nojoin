"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import { lineSelection } from "@/lib/notes/lineSelection";
import type { OutboxDraft } from "@/lib/notes/noteOutbox";
import { lineActionsFor, type LineActionId } from "@/lib/transcript/actions";
import type { RecordingSpeaker, TranscriptSegment } from "@/types";
import { lineIsSettling, LINE_SETTLING_REASON, splitWords } from "@/lib/transcript/settling";

import NoteOnSelectionPopover from "../notes/NoteOnSelectionPopover";

import type { TranscriptLineActions } from "./_hooks/useTranscriptEditing";
import TranscriptActionsMenu from "./TranscriptActionsMenu";
import { formatTime } from "./transcriptDisplay";
import { formatTimelineMs, segmentStartMs } from "@/lib/notes/placement";
import LiveLineEditor, { type LiveEditKind } from "./LiveLineEditor";
import type { LiveTranscriptPresentation } from "./liveTranscriptPresentation";

interface TranscriptLineMenuProps {
  readOnly?: boolean;
  live?: LiveTranscriptPresentation;
  speakers?: readonly RecordingSpeaker[];
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
  onSplitSegment?: ((segment: TranscriptSegment, wordId: string) => Promise<boolean>) | null;
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
  onSplitSegment = null,
  readOnly = false,
  isEditingText,
  isChangingSpeaker,
  live,
  speakers,
}: TranscriptLineMenuProps) {
  const [noteAnchor, setNoteAnchor] = useState<HTMLElement | null>(null);
  const [lineEdit, setLineEdit] = useState<{
    kind: LiveEditKind; anchor: HTMLElement; segment: TranscriptSegment;
  } | null>(null);
  /** The ⋯ button, while an editor or popover an item opened is open. */
  const returnFocusTo = useRef<HTMLElement | null>(null);
  const selection = useMemo(() => lineSelection(segment), [segment]);

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
    note: (trigger) => {
      if (live) {
        if (selection) live.onNoteOnLine?.(selection);
      } else {
        setNoteAnchor(trigger);
      }
    },
    editText: (trigger) => {
      if (live) {
        setLineEdit({ kind: "editText", anchor: trigger, segment });
        return;
      }
      returnFocusTo.current = trigger;
      actions.startTextEdit(segment, segmentId);
    },
    changeSpeaker: (trigger) => {
      if (live) {
        setLineEdit({ kind: "changeSpeaker", anchor: trigger, segment });
        return;
      }
      returnFocusTo.current = trigger;
      actions.openSpeakerPopover(segmentId, trigger);
    },
    play: () => onPlayFrom?.(segment.start),
    split: (trigger) => setLineEdit({ kind: "split", anchor: trigger, segment }),
  };

  const liveCanEdit = !readOnly && live?.lineHandlers !== undefined && segment.settling_required === true &&
    !!segment.id && segment.revision !== undefined;
  const items = lineActionsFor({
    view: live ? "live" : "finished",
    canNote: (live ? live.onNoteOnLine !== undefined : onAddNote !== null) && selection !== null,
    canEdit: live ? liveCanEdit : !isSegmentReadOnly || lineIsSettling(segment),
    provisional: segment.provisional === true,
    canPlay: !live && onPlayFrom !== null,
    canSplit: (live ? liveCanEdit : !isSegmentReadOnly && !!segment.id && segment.revision !== undefined &&
      onSplitSegment !== null) && splitWords(segment).length > 0,
    settlingRequired: segment.settling_required,
    disabledReason: lineIsSettling(segment) ? LINE_SETTLING_REASON : undefined,
  }).map((action) => ({ ...action, onSelect: run[action.id] }));
  // An editor submits against the line as it opened, so a line changed since
  // is refused rather than overwritten. It goes once the line can't take the
  // edit: it is settling again, or the view stopped offering it.
  const editHandlers = live ? live.lineHandlers
    : onSplitSegment ? { split: onSplitSegment, editText: async () => false, changeSpeaker: async () => false } : undefined;
  const editorOpen = lineEdit !== null && editHandlers !== undefined && !lineIsSettling(segment) &&
    (live ? liveCanEdit : lineEdit.kind === "split" && !isSegmentReadOnly);
  if (lineEdit !== null && !editorOpen) setLineEdit(null);

  return (
    <>
      <TranscriptActionsMenu
        label={live ? `Actions for the line at ${formatTimelineMs(segmentStartMs(segment))}`
          : `Actions for ${speakerName}'s line at ${formatTime(segment.start)}`}
        items={items}
        active={noteAnchor !== null || lineEdit !== null || opened}
        triggerClassName="ml-auto self-center -my-1 pointer-coarse:-my-2.5"
      />
      {editorOpen ? <LiveLineEditor
        key={`${lineEdit.kind}-${lineEdit.segment.id}`}
        kind={lineEdit.kind} segment={lineEdit.segment} anchor={lineEdit.anchor}
        handlers={editHandlers} speakers={speakers}
        onClose={() => { setLineEdit(null); lineEdit.anchor.focus(); }}
      /> : null}
      {noteAnchor && selection && onAddNote ? (
        <NoteOnSelectionPopover
          quote={selection.quotedText}
          title={`Note on the line at ${formatTime(segment.start)}`}
          anchor={noteAnchor}
          onSubmit={(body) => onAddNote({ kind: "selection", body, anchorMs: null, selection })}
          onClose={() => setNoteAnchor(null)}
        />
      ) : null}
    </>
  );
}
