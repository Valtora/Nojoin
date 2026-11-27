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
  // The live feed's own callbacks are stable; the feed object is new on every
  // read, and a callback built on it would redraw every live speaker's card.
  const applyLiveNames = liveTranscript?.applyLiveSpeakerNames;
  const refreshLive = liveTranscript?.refresh;
  const applyNames = useCallback((names: Record<string, string>) => {
    applyLiveNames?.(names);
  }, [applyLiveNames]);
  const onAccepted = useCallback(async () => {
    refreshLive?.();
    await onPeopleChanged?.();
  }, [refreshLive, onPeopleChanged]);
  const naming = useLiveSpeakerNaming(recording.id, applyNames, onAccepted,
    liveTranscript?.applyLiveSpeakerMerges, liveTranscript?.applyLiveSpeakerSuggestions);
  const capturing = isLiveCaptureInProgress(recording) && !readOnly;
  const panelSpeakers = useMemo(() => [...speakers], [speakers]);
  return <SpeakerPanel {...props} speakers={panelSpeakers} disableEditing={readOnly || props.disableEditing}
    liveSpeakerNames={liveTranscript?.liveSpeakerNames}
    onRenameLiveSpeaker={capturing ? naming.renameLiveSpeaker : undefined}
    onMergeLiveSpeaker={capturing ? naming.mergeLiveSpeaker : undefined}
    liveSpeakerSuggestions={capturing ? liveTranscript?.liveSpeakerSuggestions : undefined}
    liveSpeakerSuggestionSources={liveTranscript?.liveSpeakerSuggestionSources}
    onDismissLiveSpeakerSuggestion={capturing ? naming.dismissLiveSpeakerSuggestion : undefined} />;
}
