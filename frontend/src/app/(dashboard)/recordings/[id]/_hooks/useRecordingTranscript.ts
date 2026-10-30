import { useEffect, useState, type Dispatch, type SetStateAction } from "react";

import { useLiveTranscript, LIVE_TRANSCRIPT_POLL_INTERVAL_MS } from "@/components/transcript/_hooks/useLiveTranscript";
import { useOnDepsChange } from "@/lib/useOnDepsChange";
import type { Recording } from "@/types";

import { hasInFlightTranscriptFeed, isRecordingInFlight } from "./recordingDetailUtils";
import { useDetailTranscript } from "./useDetailTranscript";

/** Owns the independent readers and switches their complete presentation snapshot. */
export function useRecordingTranscript(
  recording: Recording | null,
  setRecording: Dispatch<SetStateAction<Recording | null>>,
  localCaptureActive = false,
) {
  const recordingId = recording?.id ?? null;
  const previousPublicationAvailable = recording?.previous_publication_available === true;
  const reprocessPublicationAvailable = previousPublicationAvailable || recording?.reprocess_publication_complete === true;
  const [retainedRecordingId, setRetainedRecordingId] = useState(
    reprocessPublicationAvailable && !localCaptureActive ? recordingId : null,
  );
  useOnDepsChange([recordingId, reprocessPublicationAvailable, localCaptureActive], () => {
    setRetainedRecordingId((previous) => localCaptureActive ? null
      : reprocessPublicationAvailable ? recordingId : previous === recordingId ? previous : null);
  });
  // A marker clears at publication, before notes finish. Keep the publication
  // reader selected through that boundary and any failed replacement read.
  const hasRetainedPublication = recordingId !== null && retainedRecordingId === recordingId;
  const inFlight = (isRecordingInFlight(recording) || localCaptureActive) && !hasRetainedPublication;
  const [inFlightRecordingId, setInFlightRecordingId] = useState(inFlight ? recordingId : null);
  useOnDepsChange([recordingId, inFlight], () => {
    setInFlightRecordingId((previous) => inFlight ? recordingId : previous === recordingId ? previous : null);
  });
  const handoffRequired = recordingId !== null && inFlightRecordingId === recordingId;
  const settled = useDetailTranscript(recording, setRecording, {
    authoritativeEmpty: handoffRequired || hasRetainedPublication,
    inFlight,
    retainedPublication: hasRetainedPublication,
  });
  const { isSettledTranscriptReady, syncTranscriptState } = settled;
  // Once the run has ended and its result is on screen, the recording reads
  // as any other again, polled for later changes.
  const runEnded = hasRetainedPublication && !reprocessPublicationAvailable && !isRecordingInFlight(recording) &&
    isSettledTranscriptReady && settled.deferredTranscriptUtteranceIds.length === 0;
  useOnDepsChange([runEnded], () => {
    if (runEnded) setRetainedRecordingId(null);
  });
  // While in flight, only a capture, a finished transcript a later step runs
  // over, or a settled meeting's reprocess (which keeps its settled lines) has
  // lines to read. An import or any other reprocess shows its processing state
  // until it publishes, with no feed polling a transcript it lacks.
  const liveFeed = inFlight && (localCaptureActive || hasInFlightTranscriptFeed(recording));
  // The run this page read the feed for keeps it until the settled read takes
  // over, and a later run with nothing to read starts without it.
  const [feedRecordingId, setFeedRecordingId] = useState(liveFeed ? recordingId : null);
  useOnDepsChange([recordingId, inFlight, liveFeed, isSettledTranscriptReady], () => {
    setFeedRecordingId((previous) => liveFeed ? recordingId
      : inFlight || isSettledTranscriptReady || previous !== recordingId ? null : previous);
  });
  const feedHandoff = recordingId !== null && feedRecordingId === recordingId;
  const usesInFlightTranscript = !hasRetainedPublication &&
    (liveFeed || (feedHandoff && !isSettledTranscriptReady));
  // Keep this reader alive through Stop, pipeline work and a failed final read.
  // The settled reader has its own cursor; neither reader ever seeds the other.
  const feed = useLiveTranscript(recording, usesInFlightTranscript);
  // On resume, keep the last accepted same-recording view until the new feed's
  // first answer. A successfully loaded empty feed is still authoritative.
  const showFeed = usesInFlightTranscript && feed.hasLoaded;
  const transcriptSegments = showFeed ? feed.segments : settled.transcriptSegments;
  const transcriptSpeakers = showFeed ? feed.speakers
    : hasRetainedPublication ? settled.publicationSpeakers ?? recording?.speakers : recording?.speakers;
  const transcriptSpeakerNames = showFeed
    ? feed.liveSpeakerNames
    : settled.liveSpeakerNames?.recordingId === recordingId ? settled.liveSpeakerNames.names : {};
  const liveTranscript = {
    ...feed,
    segments: transcriptSegments,
    speakers: transcriptSpeakers ?? [],
    liveSpeakerNames: transcriptSpeakerNames,
  };

  useEffect(() => {
    if (inFlight || (!handoffRequired && !hasRetainedPublication) || isSettledTranscriptReady) return;
    const interval = setInterval(() => {
      syncTranscriptState("full").catch((error) => {
        console.error("Transcript handoff failed", error);
      });
    }, LIVE_TRANSCRIPT_POLL_INTERVAL_MS);
    return () => clearInterval(interval);
  }, [inFlight, handoffRequired, hasRetainedPublication, isSettledTranscriptReady, syncTranscriptState]);

  return {
    ...settled,
    liveTranscript,
    usesInFlightTranscript,
    hasRetainedPublication,
    transcriptIsAuthoritative: (handoffRequired || hasRetainedPublication) && !usesInFlightTranscript && isSettledTranscriptReady,
    transcriptSegments,
    transcriptSpeakers,
    transcriptSpeakerNames,
  };
}
