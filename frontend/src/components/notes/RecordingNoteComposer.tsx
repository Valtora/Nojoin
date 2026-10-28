"use client";

import { useCallback, type RefObject } from "react";

import { CAPTURE_NOTE_BODY, captureShortcutLabel, useCaptureActions } from "@/lib/captures/useCaptureActions";
import { isLiveCaptureInProgress } from "@/lib/liveCapture";
import type { AudioClock } from "@/lib/notes/audioClock";
import { segmentStartMs } from "@/lib/notes/placement";
import { useNoteOutbox } from "@/lib/notes/useNoteOutbox";
import { RecordingStatus, type Recording, type TranscriptSegment } from "@/types";

import LiveNoteComposer from "./LiveNoteComposer";

interface Props {
  recording: Recording;
  segments: readonly TranscriptSegment[];
  audioClock?: AudioClock;
  boxRef?: RefObject<HTMLTextAreaElement | null>;
}

/** One draft owner and screen-capture shortcut for every recording state. */
export default function RecordingNoteComposer({ recording, segments, audioClock, boxRef }: Props) {
  const { outbox, snapshot } = useNoteOutbox(recording.id);
  const activeCapture = isLiveCaptureInProgress(recording);
  const readAudioNow = useCallback(() => audioClock?.audioNow(recording.id) ??
    (activeCapture
      ? segments.reduce((latest, segment) => Math.max(latest, segmentStartMs(segment)), 0)
      : Math.round((recording.duration_seconds ?? 0) * 1000)),
  [audioClock, recording.id, recording.duration_seconds, activeCapture, segments]);
  const onFrame = useCallback((file: File, atMs: number) =>
    outbox.submit({ kind: "point", body: CAPTURE_NOTE_BODY, anchorMs: atMs }, { file }), [outbox]);
  const capture = useCaptureActions(recording.id, readAudioNow, onFrame);

  return <LiveNoteComposer
    captureActive={activeCapture}
    finished={recording.status === RecordingStatus.PROCESSED || recording.status === RecordingStatus.ERROR}
    readAudioNow={readAudioNow}
    onSubmit={(note) => outbox.submit(note, { clearDraft: true })}
    draft={snapshot.draft}
    onDraftChange={outbox.setDraft}
    notice={snapshot.notice}
    boxRef={boxRef}
    draftFile={snapshot.draftFile}
    onDraftFileChange={outbox.setDraftFile}
    onCapture={capture.canCaptureScreen ? () => void capture.captureScreen() : undefined}
    capturing={capture.busy}
    captureShortcut={captureShortcutLabel()}
    captureMessage={capture.message}
  />;
}
