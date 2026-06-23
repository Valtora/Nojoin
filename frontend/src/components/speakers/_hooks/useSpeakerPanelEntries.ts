"use client";

import { useCallback, useMemo } from "react";

import {
  GlobalSpeaker,
  RecordingSpeaker,
  TranscriptSegment,
} from "@/types";
import {
  buildGlobalSpeakerById,
  buildUniqueGlobalSpeakerIdByName,
  getRecordingSpeakerDisplayName,
  getRecordingSpeakerGroupKey,
  getResolvedGlobalSpeakerId,
} from "@/lib/recordingSpeakerUtils";

/** A grouped speaker row rendered by the speaker panel. */
export interface SpeakerPanelEntry {
  key: string;
  speaker: RecordingSpeaker;
  members: RecordingSpeaker[];
  labels: string[];
  displayName: string;
  hasVoiceprint: boolean;
}

export interface UseSpeakerPanelEntriesResult {
  /** Grouped, sorted speaker rows. */
  speakerEntries: SpeakerPanelEntry[];
  /** Resolve a recording speaker's display name (global override aware). */
  getSpeakerName: (speaker: RecordingSpeaker) => string;
  /** Number of transcript segments keyed by diarization label. */
  segmentCountByLabel: Map<string, number>;
}

/**
 * Derives the grouped speaker rows for {@link SpeakerPanel} from the raw
 * recording speakers, segments, and global speaker library. Extracted from
 * SpeakerPanel verbatim (FE-012) so the grouping/sorting logic is isolated and
 * independently reasoned about; output is unchanged.
 */
export function useSpeakerPanelEntries(
  speakers: RecordingSpeaker[],
  segments: TranscriptSegment[],
  globalSpeakers: GlobalSpeaker[],
): UseSpeakerPanelEntriesResult {
  const globalSpeakerById = useMemo(
    () => buildGlobalSpeakerById(globalSpeakers),
    [globalSpeakers],
  );

  const uniqueGlobalSpeakerIdByName = useMemo(
    () => buildUniqueGlobalSpeakerIdByName(speakers, globalSpeakerById),
    [globalSpeakerById, speakers],
  );

  const getSpeakerName = useCallback(
    (speaker: RecordingSpeaker): string =>
      getRecordingSpeakerDisplayName(speaker, globalSpeakerById),
    [globalSpeakerById],
  );

  const segmentCountByLabel = useMemo(() => {
    const counts = new Map<string, number>();

    segments.forEach((segment) => {
      counts.set(segment.speaker, (counts.get(segment.speaker) || 0) + 1);
    });

    return counts;
  }, [segments]);

  const speakerEntries = useMemo(() => {
    const groupedSpeakers = new Map<string, RecordingSpeaker[]>();

    speakers
      .filter((speaker) => !speaker.merged_into_id)
      .forEach((speaker) => {
        const key = getRecordingSpeakerGroupKey(
          speaker,
          globalSpeakerById,
          uniqueGlobalSpeakerIdByName,
        );
        const existing = groupedSpeakers.get(key);

        if (existing) {
          existing.push(speaker);
          return;
        }

        groupedSpeakers.set(key, [speaker]);
      });

    return Array.from(groupedSpeakers.entries())
      .map(([key, members]) => {
        const sortedMembers = [...members].sort((left, right) => {
          const segmentCountDiff =
            (segmentCountByLabel.get(right.diarization_label) || 0) -
            (segmentCountByLabel.get(left.diarization_label) || 0);

          if (segmentCountDiff !== 0) {
            return segmentCountDiff;
          }

          return getSpeakerName(left).localeCompare(getSpeakerName(right));
        });

        const representative =
          sortedMembers.find((speaker) => getResolvedGlobalSpeakerId(speaker)) ||
          sortedMembers[0];
        const globalSpeakerId = getResolvedGlobalSpeakerId(representative);
        const hasVoiceprint =
          sortedMembers.some((speaker) => speaker.has_voiceprint) ||
          !!(globalSpeakerId && globalSpeakerById.get(globalSpeakerId)?.has_voiceprint);

        return {
          key,
          speaker: representative,
          members: sortedMembers,
          labels: sortedMembers.map((speaker) => speaker.diarization_label),
          displayName: getSpeakerName(representative),
          hasVoiceprint,
        } satisfies SpeakerPanelEntry;
      })
      .sort((left, right) => {
        return left.displayName.localeCompare(right.displayName);
      });
  }, [
    getSpeakerName,
    globalSpeakerById,
    segmentCountByLabel,
    speakers,
    uniqueGlobalSpeakerIdByName,
  ]);

  return { speakerEntries, getSpeakerName, segmentCountByLabel };
}
