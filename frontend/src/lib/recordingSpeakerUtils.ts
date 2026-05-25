import { GlobalSpeaker, RecordingSpeaker, TranscriptSegment } from "@/types";

const MEETING_COLOR_PRIORITY = [
  "blue",
  "orange",
  "green",
  "violet",
  "red",
  "cyan",
  "amber",
  "pink",
  "teal",
  "indigo",
  "lime",
  "rose",
  "emerald",
  "yellow",
  "fuchsia",
  "sky",
  "purple",
] as const;

type MeetingColorKey = (typeof MEETING_COLOR_PRIORITY)[number];

const MEETING_COLOR_HUES: Record<MeetingColorKey, number> = {
  red: 0,
  rose: 15,
  pink: 30,
  orange: 45,
  amber: 60,
  yellow: 75,
  lime: 100,
  green: 125,
  emerald: 145,
  teal: 165,
  cyan: 185,
  sky: 205,
  blue: 225,
  indigo: 245,
  violet: 265,
  purple: 285,
  fuchsia: 315,
};

const stableHash = (value: string): number => {
  let hash = 0;
  for (let index = 0; index < value.length; index += 1) {
    hash = value.charCodeAt(index) + ((hash << 5) - hash);
  }
  return Math.abs(hash);
};

const normalisePaletteColorKey = (
  color: string | null | undefined,
): MeetingColorKey | undefined => {
  if (!color) {
    return undefined;
  }

  const normalised = color.toLowerCase();
  return MEETING_COLOR_PRIORITY.find((colorKey) => colorKey === normalised);
};

const rotateMeetingColorPriority = (seed: string): MeetingColorKey[] => {
  const offset = stableHash(seed) % MEETING_COLOR_PRIORITY.length;
  return [
    ...MEETING_COLOR_PRIORITY.slice(offset),
    ...MEETING_COLOR_PRIORITY.slice(0, offset),
  ];
};

const getMeetingColorDistance = (
  left: MeetingColorKey,
  right: MeetingColorKey,
): number => {
  const leftHue = MEETING_COLOR_HUES[left] ?? 0;
  const rightHue = MEETING_COLOR_HUES[right] ?? 0;
  const rawDistance = Math.abs(leftHue - rightHue);
  return Math.min(rawDistance, 360 - rawDistance);
};

const chooseMeetingLocalColor = (
  speakerLabel: string,
  usedColors: Set<MeetingColorKey>,
): MeetingColorKey => {
  const availableColors = MEETING_COLOR_PRIORITY.filter(
    (color) => !usedColors.has(color),
  );
  const candidates = availableColors.length > 0
    ? availableColors
    : [...MEETING_COLOR_PRIORITY];
  const priority = rotateMeetingColorPriority(speakerLabel);
  const priorityIndex = new Map<MeetingColorKey, number>(
    priority.map((color, index) => [color, index]),
  );

  if (usedColors.size === 0) {
    return priority.find((color) => candidates.includes(color)) ?? candidates[0];
  }

  let bestColor = candidates[0];
  let bestDistance = -1;
  let bestTieBreak = Number.MAX_SAFE_INTEGER;

  candidates.forEach((candidate) => {
    const candidateDistance = Array.from(usedColors).reduce(
      (minimumDistance, usedColor) =>
        Math.min(minimumDistance, getMeetingColorDistance(candidate, usedColor)),
      Number.POSITIVE_INFINITY,
    );
    const tieBreak = priorityIndex.get(candidate) ?? Number.MAX_SAFE_INTEGER;

    if (
      candidateDistance > bestDistance ||
      (candidateDistance === bestDistance && tieBreak < bestTieBreak)
    ) {
      bestColor = candidate;
      bestDistance = candidateDistance;
      bestTieBreak = tieBreak;
    }
  });

  return bestColor;
};

const getSpeakerAliases = (speaker: RecordingSpeaker): string[] => {
  const aliases = [
    speaker.diarization_label,
    speaker.name,
    speaker.local_name,
    speaker.global_speaker?.name,
  ].filter((alias): alias is string => Boolean(alias));

  return Array.from(new Set(aliases));
};

const getExistingMeetingLocalColor = (
  aliases: Iterable<string>,
  existingColors: Record<string, string>,
): MeetingColorKey | undefined => {
  for (const alias of aliases) {
    const existingColor = normalisePaletteColorKey(existingColors[alias]);
    if (existingColor) {
      return existingColor;
    }
  }

  return undefined;
};

export const buildMeetingSpeakerColors = ({
  segments,
  speakers,
  existingColors = {},
}: {
  segments: Array<Pick<TranscriptSegment, "speaker" | "overlapping_speakers">>;
  speakers?: RecordingSpeaker[];
  existingColors?: Record<string, string>;
}): Record<string, string> => {
  const aliasToCanonical = new Map<string, string>();
  const aliasesByCanonical = new Map<string, Set<string>>();
  const orderedCanonicals: string[] = [];
  const persistedLocalColors = new Map<string, MeetingColorKey>();

  const rememberAlias = (alias: string, canonical?: string) => {
    const resolvedCanonical = canonical ?? aliasToCanonical.get(alias) ?? alias;
    aliasToCanonical.set(alias, resolvedCanonical);

    const aliases = aliasesByCanonical.get(resolvedCanonical) ?? new Set<string>();
    aliases.add(alias);
    aliasesByCanonical.set(resolvedCanonical, aliases);

    return resolvedCanonical;
  };

  const rememberCanonical = (canonical: string) => {
    if (!orderedCanonicals.includes(canonical)) {
      orderedCanonicals.push(canonical);
    }
  };

  (speakers ?? []).forEach((speaker) => {
    const canonical = speaker.diarization_label;
    rememberAlias(canonical, canonical);
    getSpeakerAliases(speaker).forEach((alias) => rememberAlias(alias, canonical));

    const persistedLocalColor = normalisePaletteColorKey(speaker.color);
    if (persistedLocalColor) {
      persistedLocalColors.set(canonical, persistedLocalColor);
    }
  });

  segments.forEach((segment) => {
    [segment.speaker, ...(segment.overlapping_speakers ?? [])].forEach((label) => {
      const canonical = rememberAlias(label);
      rememberCanonical(canonical);
    });
  });

  (speakers ?? []).forEach((speaker) => {
    rememberCanonical(speaker.diarization_label);
  });

  const nextColors: Record<string, string> = {};
  const usedColors = new Set<MeetingColorKey>();

  orderedCanonicals.forEach((canonical) => {
    const aliases = aliasesByCanonical.get(canonical) ?? new Set([canonical]);
    const existingMeetingLocalColor = getExistingMeetingLocalColor(
      aliases,
      existingColors,
    );
    const persistedLocalColor = persistedLocalColors.get(canonical);
    const color =
      existingMeetingLocalColor ??
      persistedLocalColor ??
      chooseMeetingLocalColor(canonical, usedColors);

    aliases.forEach((alias) => {
      nextColors[alias] = color;
    });
    nextColors[canonical] = color;
    usedColors.add(color);
  });

  return nextColors;
};

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

  return `name:${normaliseSpeakerName(
    getRecordingSpeakerDisplayName(speaker, globalSpeakerById),
  )}`;
};