import type { ReactNode } from "react";

import TranscriptView from "@/components/TranscriptView";
import type { LiveLineHandlers } from "@/components/transcript/LiveLineEditor";
import type { MoveNoteHandler } from "@/components/transcript/_hooks/useNoteMoves";
import type { InlineNote, NoteSelection } from "@/lib/notes/noteOutbox";
import { buildMeetingSpeakerColors, getRecordingSpeakerDisplayName } from "@/lib/recordingSpeakerUtils";
import type { RecordingSpeaker, TimelineNote, TranscriptSegment } from "@/types";

interface SharedLiveTranscriptProps {
  lineHandlers?: LiveLineHandlers;
  speakers?: readonly RecordingSpeaker[];
  segments: TranscriptSegment[];
  hasLoaded: boolean;
  isPaused?: boolean;
  notes?: readonly (InlineNote | TimelineNote)[];
  onRetryNote?: (key: string) => void;
  onDiscardNote?: (key: string) => void;
  onMoveNote?: MoveNoteHandler;
  footer?: ReactNode;
  onNoteOnLine?: (selection: NoteSelection) => void;
  onNoteAtTime?: (atMs: number) => void;
  liveSpeakerNames?: Readonly<Record<string, string>>;
  onRenameLiveSpeaker?: (label: string, name: string) => Promise<boolean>;
  loadPeopleNames?: () => Promise<readonly string[]>;
}

const noop = () => {};

/** Runs legacy live behavioural fixtures through the production shared cards. */
export default function SharedLiveTranscript({
  segments, hasLoaded, isPaused, lineHandlers, speakers = [], notes, onRetryNote,
  onDiscardNote, onMoveNote, footer, onNoteOnLine, onNoteAtTime, liveSpeakerNames = {},
  onRenameLiveSpeaker, loadPeopleNames,
}: SharedLiveTranscriptProps) {
  const speakerMap = {
    ...Object.fromEntries(speakers.map((speaker) => [
      speaker.diarization_label, getRecordingSpeakerDisplayName(speaker, new Map()),
    ])),
    ...liveSpeakerNames,
  };
  return <>
    <TranscriptView recordingId="rec-1" segments={segments} currentTime={0}
      onPlaySegment={noop} isPlaying={false} onPause={noop} onResume={noop}
      speakerMap={speakerMap} speakers={speakers} globalSpeakers={[]}
      onRenameSpeaker={noop} onUpdateSegmentSpeaker={noop} onUpdateSegmentText={noop}
      onFindAndReplace={noop} speakerColors={buildMeetingSpeakerColors({ segments, speakers: [...speakers] })}
      onUndo={noop} onRedo={noop} canUndo={false} canRedo={false} onExport={noop}
      disableSegmentPlayback disableExport timelineNotes={notes} onRetryNote={onRetryNote}
      onDiscardNote={onDiscardNote} onMoveNote={onMoveNote}
      live={{ hasLoaded, isPaused, lineHandlers, onNoteOnLine, onNoteAtTime, onRenameLiveSpeaker, loadPeopleNames }} />
    {footer}
  </>;
}
