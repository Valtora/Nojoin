import { useRef, type ComponentProps } from "react";

import TranscriptSection from "@/app/(dashboard)/recordings/[id]/_components/TranscriptSection";
import RecordingStatusDisplay from "@/components/RecordingStatusDisplay";
import { useNoteEdits, type NoteEdits } from "@/lib/notes/useNoteEdits";

const noop = () => {};
type Props = ComponentProps<typeof RecordingStatusDisplay> & { noteEdits?: NoteEdits };

/** The production section/body boundary, for the retained live integration tests. */
export default function RecordingTranscriptWithLiveBody({ noteEdits: suppliedEdits, ...props }: Props) {
  const composerBoxRef = useRef<HTMLTextAreaElement>(null);
  const localEdits = useNoteEdits(props.recording.id);
  return <TranscriptSection active recording={props.recording} liveTranscript={props.liveTranscript}
    noteEdits={suppliedEdits ?? localEdits} timelineNotes={props.timelineNotes ?? []}
    liveNoteBoxRef={composerBoxRef} transcriptSegments={props.liveTranscript.segments}
    inFlightContent={<RecordingStatusDisplay {...props} composerBoxRef={composerBoxRef} />}
    currentTime={-1} isPlaying={false} speakerMap={{}} speakerColors={{}} globalSpeakers={[]}
    canUndo={false} canRedo={false} deferredTranscriptUtteranceIds={[]} canPlayback={false} canExport={false}
    onPlaySegment={noop} onPause={noop} onResume={noop} onRenameSpeaker={noop}
    onRefresh={noop} onUpdateSegmentSpeaker={noop} onUpdateSegmentText={noop}
    onFindAndReplace={noop} onUndo={noop} onRedo={noop} onExport={noop}
    onActiveEditUtteranceChange={noop} />;
}
