"use client";

import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";

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
import LiveLineEditor, { type LiveEditKind, type LiveLineHandlers } from "./LiveLineEditor";
import type { LiveInteractionChange, LiveTranscriptPresentation } from "./liveTranscriptPresentation";
import type { IndexedTranscriptSegment } from "./transcriptGroups";

interface TranscriptLineMenuProps {
  readOnly?: boolean;
  live?: LiveTranscriptPresentation;
  index?: number;
  renderIdentity?: string;
  onLiveInteractionChange?: LiveInteractionChange;
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
  captureSplitSegment?: () => ((segment: TranscriptSegment, wordId: string) => Promise<boolean>) | null | undefined;
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
  captureSplitSegment,
  readOnly = false,
  isEditingText,
  isChangingSpeaker,
  live,
  speakers,
  index = 0,
  renderIdentity = segmentId,
  onLiveInteractionChange,
}: TranscriptLineMenuProps) {
  const [noteAnchor, setNoteAnchor] = useState<HTMLElement | null>(null);
  const [lineEdit, setLineEdit] = useState<{
    kind: LiveEditKind;
    anchor: HTMLElement;
    segment: TranscriptSegment;
    handlers: LiveLineHandlers;
    speakers?: readonly RecordingSpeaker[];
    liveContext?: IndexedTranscriptSegment;
  } | null>(null);
  const currentLineEdit = useRef(lineEdit);
  useLayoutEffect(() => {
    currentLineEdit.current = lineEdit;
    return () => { currentLineEdit.current = null; };
  }, [lineEdit]);
  /** The ⋯ button, while an editor or popover an item opened is open. */
  const returnFocusTo = useRef<HTMLElement | null>(null);
  const selection = useMemo(() => lineSelection(segment), [segment]);
  const liveContext = lineEdit?.liveContext;
  useEffect(() => {
    if (!liveContext) return;
    const identity = `${renderIdentity}:command`;
    onLiveInteractionChange?.(identity, true, liveContext);
    return () => onLiveInteractionChange?.(identity, false);
  }, [liveContext, renderIdentity, onLiveInteractionChange]);

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
        if (live.lineHandlers) setLineEdit({ kind: "editText", anchor: trigger, segment, handlers: live.lineHandlers, speakers,
          liveContext: { segment, index, segmentId } });
        return;
      }
      returnFocusTo.current = trigger;
      actions.startTextEdit(segment, segmentId);
    },
    changeSpeaker: (trigger) => {
      if (live) {
        if (live.lineHandlers) setLineEdit({ kind: "changeSpeaker", anchor: trigger, segment, handlers: live.lineHandlers, speakers,
          liveContext: { segment, index, segmentId } });
        return;
      }
      returnFocusTo.current = trigger;
      actions.openSpeakerPopover(segmentId, trigger);
    },
    play: () => onPlayFrom?.(segment.start),
    split: (trigger) => {
      const split = captureSplitSegment ? captureSplitSegment() : onSplitSegment;
      const handlers = live?.lineHandlers ?? (split ? {
        split,
        editText: async () => false,
        changeSpeaker: async () => false,
      } : null);
      if (handlers) setLineEdit({ kind: "split", anchor: trigger, segment, handlers, speakers,
        liveContext: live ? { segment, index, segmentId } : undefined });
    },
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

  return (
    <>
      <TranscriptActionsMenu
        label={live ? `Actions for the line at ${formatTimelineMs(segmentStartMs(segment))}`
          : `Actions for ${speakerName}'s line at ${formatTime(segment.start)}`}
        items={items}
        active={noteAnchor !== null || lineEdit !== null || opened}
        triggerClassName="ml-auto self-center -my-1 pointer-coarse:-my-2.5"
      />
      {lineEdit ? <LiveLineEditor
        key={`${lineEdit.kind}-${lineEdit.segment.id}`}
        kind={lineEdit.kind} segment={lineEdit.segment} anchor={lineEdit.anchor}
        handlers={lineEdit.handlers}
        speakers={lineEdit.speakers}
        onClose={() => {
          if (currentLineEdit.current !== lineEdit) return;
          setLineEdit((current) => current === lineEdit ? null : current);
          lineEdit.anchor.focus();
        }}
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
