"use client";

import { useCallback, useMemo, type ComponentProps } from "react";
import type { Recording, RecordingSpeaker } from "@/types";
import { isLiveCaptureInProgress } from "@/lib/liveCapture";
import SpeakerPanel from "@/components/SpeakerPanel";
import { useLiveSpeakerNaming } from "@/components/transcript/_hooks/useLiveSpeakerNaming";
import type { LiveTranscript } from "@/components/transcript/_hooks/useLiveTranscript";

type Props = Omit<ComponentProps<typeof SpeakerPanel>, "speakers"> & {
  speakers: readonly RecordingSpeaker[];
  recording: Recording;
  liveTranscript?: LiveTranscript;
  readOnly?: boolean;
  onPeopleChanged?: () => Promise<void> | void;
};

/** One speaker presentation for desktop and mobile, including identities without final rows. */
export default function RecordingSpeakers({ recording, liveTranscript, readOnly = false,
  onPeopleChanged, speakers, ...props }: Props) {
  const applyNames = useCallback((names: Record<string, string>) => {
    liveTranscript?.applyLiveSpeakerNames(names);
  }, [liveTranscript]);
  const onAccepted = useCallback(async () => {
    liveTranscript?.refresh();
    await onPeopleChanged?.();
  }, [liveTranscript, onPeopleChanged]);
  const naming = useLiveSpeakerNaming(recording.id, applyNames, onAccepted);
  const panelSpeakers = useMemo(() => [...speakers], [speakers]);
  return <SpeakerPanel {...props} speakers={panelSpeakers} disableEditing={readOnly || props.disableEditing}
    liveSpeakerNames={liveTranscript?.liveSpeakerNames}
    onRenameLiveSpeaker={isLiveCaptureInProgress(recording) && !readOnly ? naming.renameLiveSpeaker : undefined} />;
}
