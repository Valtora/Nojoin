"use client";

import { useCallback } from "react";

import { TranscriptSegment } from "@/types";
import { useNotificationStore } from "@/lib/notificationStore";

export interface UseSpeakerSnippetPlaybackOptions {
  segments: TranscriptSegment[];
  currentTime: number;
  isPlaying: boolean;
  onPlaySegment: (time: number, end?: number) => void;
  onPause: () => void;
  onResume: () => void;
}

export interface SpeakerSnippetPlayback {
  /**
   * Toggle/preview playback for a speaker's labels. When the speaker's lane is
   * already active this pauses/resumes; otherwise it plays a random snippet.
   */
  playSnippet: (labels: string[], isEntryActive: boolean) => void;
  /** Play the next (preferably not-currently-playing) snippet for the labels. */
  nextSnippet: (labels: string[]) => void;
}

/**
 * Encapsulates the speaker preview/next-snippet playback behaviour previously
 * inlined in {@link SpeakerPanel} (FE-012). Behaviour is unchanged, including
 * the warning notification when a speaker has no audio segments and the random
 * snippet selection.
 */
export function useSpeakerSnippetPlayback(
  options: UseSpeakerSnippetPlaybackOptions,
): SpeakerSnippetPlayback {
  const { segments, currentTime, isPlaying, onPlaySegment, onPause, onResume } =
    options;
  const { addNotification } = useNotificationStore();

  const playSnippet = useCallback(
    (labels: string[], isEntryActive: boolean) => {
      if (isEntryActive) {
        if (isPlaying) {
          onPause();
        } else {
          onResume();
        }
        return;
      }

      const labelSet = new Set(labels);
      const speakerSegments = segments.filter((segment) =>
        labelSet.has(segment.speaker),
      );
      if (speakerSegments.length === 0) {
        addNotification({
          type: "warning",
          message: "No audio segments found for this speaker.",
        });
        return;
      }
      const randomSegment =
        speakerSegments[Math.floor(Math.random() * speakerSegments.length)];
      onPlaySegment(randomSegment.start, randomSegment.end);
    },
    [addNotification, isPlaying, onPause, onPlaySegment, onResume, segments],
  );

  const nextSnippet = useCallback(
    (labels: string[]) => {
      const labelSet = new Set(labels);
      const speakerSegments = segments.filter((segment) =>
        labelSet.has(segment.speaker),
      );
      if (speakerSegments.length === 0) {
        addNotification({
          type: "warning",
          message: "No audio segments found for this speaker.",
        });
        return;
      }

      // Try to find a segment that is not currently playing
      let candidates = speakerSegments;
      if (speakerSegments.length > 1) {
        candidates = speakerSegments.filter(
          (s) => !(currentTime >= s.start && currentTime < s.end),
        );
        if (candidates.length === 0) candidates = speakerSegments;
      }

      const randomSegment =
        candidates[Math.floor(Math.random() * candidates.length)];
      onPlaySegment(randomSegment.start, randomSegment.end);
    },
    [addNotification, currentTime, onPlaySegment, segments],
  );

  return { playSnippet, nextSnippet };
}
