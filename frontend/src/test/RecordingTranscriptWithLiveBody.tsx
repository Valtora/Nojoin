import { useRef } from "react";

import TranscriptSection from "@/app/(dashboard)/recordings/[id]/_components/TranscriptSection";
import RecordingNoteComposer from "@/components/notes/RecordingNoteComposer";
import RecordingMyNotes from "@/components/notes/RecordingMyNotes";
import DocumentsSection from "@/app/(dashboard)/recordings/[id]/_components/DocumentsSection";
import MeetingEdgePanel from "@/components/MeetingEdgePanel";
import type { LiveTranscript } from "@/components/transcript/_hooks/useLiveTranscript";
import type { AudioClock } from "@/lib/notes/audioClock";
import { isLiveCaptureInProgress } from "@/lib/liveCapture";
import type { Recording, TimelineNote } from "@/types";
import { useNoteEdits, type NoteEdits } from "@/lib/notes/useNoteEdits";

const noop = () => {};
type Props = {
  recording: Recording;
  liveTranscript: LiveTranscript;
  noteEdits?: NoteEdits;
  audioClock?: AudioClock;
  timelineNotes?: readonly TimelineNote[] | null;
  onSaveMeetingEdgeFocus: (focus: string) => Promise<void>;
  showMeetingEdge?: boolean;
};

/** The production section/body boundary, for the retained live integration tests. */
export default function RecordingTranscriptWithLiveBody({ noteEdits: suppliedEdits, ...props }: Props) {
  const composerBoxRef = useRef<HTMLTextAreaElement>(null);
  const localEdits = useNoteEdits(props.recording.id);
  const edits = suppliedEdits ?? localEdits;
  return <><TranscriptSection active recording={props.recording} liveTranscript={props.liveTranscript}
    noteEdits={suppliedEdits ?? localEdits} timelineNotes={props.timelineNotes ?? []}
    liveNoteBoxRef={composerBoxRef} transcriptSegments={props.liveTranscript.segments}
    inFlightContent={<RecordingNoteComposer recording={props.recording} segments={props.liveTranscript.segments}
      audioClock={props.audioClock} boxRef={composerBoxRef} />}
    currentTime={-1} isPlaying={false} speakerMap={{}} speakerColors={{}} globalSpeakers={[]}
    canUndo={false} canRedo={false} deferredTranscriptUtteranceIds={[]} canPlayback={false} canExport={false}
    onPlaySegment={noop} onPause={noop} onResume={noop} onRenameSpeaker={noop}
    onRefresh={noop} onUpdateSegmentSpeaker={noop} onUpdateSegmentText={noop}
    onFindAndReplace={noop} onUndo={noop} onRedo={noop} onExport={noop}
    onActiveEditUtteranceChange={noop} />
    <RecordingMyNotes recording={props.recording} timelineNotes={props.timelineNotes ?? null}
      segments={props.liveTranscript.segments} noteEdits={edits} onShowTranscript={noop} onPlay={noop} />
    <DocumentsSection active recordingId={props.recording.id} />
    <MeetingEdgePanel readOnly={!isLiveCaptureInProgress(props.recording)}
      payload={props.recording.transcript?.meeting_edge_payload} focusText={props.recording.transcript?.meeting_edge_focus}
      status={props.recording.transcript?.meeting_edge_status} onSaveFocus={props.onSaveMeetingEdgeFocus} />
  </>;
}
