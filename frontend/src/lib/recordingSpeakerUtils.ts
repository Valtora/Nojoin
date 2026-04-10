import { GlobalSpeaker, RecordingSpeaker } from "@/types";

export const normaliseSpeakerName = (name: string): string => {
  return name.trim().toLocaleLowerCase();
};

export const buildGlobalSpeakerById = (
  globalSpeakers: GlobalSpeaker[],
): Map<number, GlobalSpeaker> => {
  return new Map(globalSpeakers.map((speaker) => [speaker.id, speaker]));
};

export const getResolvedGlobalSpeakerId = (
  speaker: RecordingSpeaker,
): number | undefined => {
  return speaker.global_speaker_id ?? speaker.global_speaker?.id;
};

export const getRecordingSpeakerDisplayName = (
  speaker: RecordingSpeaker,
  globalSpeakerById: Map<number, GlobalSpeaker>,
): string => {
  const globalSpeakerId = getResolvedGlobalSpeakerId(speaker);

  return (
    speaker.local_name ||
    speaker.global_speaker?.name ||
    (globalSpeakerId
      ? globalSpeakerById.get(globalSpeakerId)?.name
      : undefined) ||
    speaker.name ||
    speaker.diarization_label
  );
};

export const buildUniqueGlobalSpeakerIdByName = (
  speakers: RecordingSpeaker[],
  globalSpeakerById: Map<number, GlobalSpeaker>,
): Map<string, number> => {
  const idsByName = new Map<string, Set<number>>();

  speakers.forEach((speaker) => {
    const globalSpeakerId = getResolvedGlobalSpeakerId(speaker);

    if (!globalSpeakerId) {
      return;
    }

    const normalisedName = normaliseSpeakerName(
      getRecordingSpeakerDisplayName(speaker, globalSpeakerById),
    );
    const existingIds = idsByName.get(normalisedName) ?? new Set<number>();

    existingIds.add(globalSpeakerId);
    idsByName.set(normalisedName, existingIds);
  });

  const uniqueGlobalSpeakerIdByName = new Map<string, number>();

  idsByName.forEach((ids, normalisedName) => {
    if (ids.size !== 1) {
      return;
    }

    const [onlyId] = Array.from(ids);
    uniqueGlobalSpeakerIdByName.set(normalisedName, onlyId);
  });

  return uniqueGlobalSpeakerIdByName;
};

export const getRecordingSpeakerGroupKey = (
  speaker: RecordingSpeaker,
  globalSpeakerById: Map<number, GlobalSpeaker>,
  uniqueGlobalSpeakerIdByName: Map<string, number>,
): string => {
  const explicitGlobalSpeakerId = getResolvedGlobalSpeakerId(speaker);

  if (explicitGlobalSpeakerId) {
    return `global:${explicitGlobalSpeakerId}`;
  }

  const inferredGlobalSpeakerId = uniqueGlobalSpeakerIdByName.get(
    normaliseSpeakerName(
      getRecordingSpeakerDisplayName(speaker, globalSpeakerById),
    ),
  );

  if (inferredGlobalSpeakerId) {
    return `global:${inferredGlobalSpeakerId}`;
  }

  return `label:${speaker.diarization_label}`;
};