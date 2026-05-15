import { getMatchScore } from "@/lib/searchUtils";

import type { SettingsAutosaveSnapshot } from "./SettingsAutosaveState";
import { TAB_KEYWORDS } from "./keywords";
import type { SettingsSectionId } from "./settingsMetadata";

export type SettingsSectionMatchScores = Record<SettingsSectionId, number>;

const AUTOSAVE_STATE_PRIORITY: Record<SettingsAutosaveSnapshot["status"], number> = {
  blocked: 4,
  error: 3,
  saving: 2,
  pending: 1,
  saved: 0,
};

export function mergeAutosaveStates(
  ...states: Array<SettingsAutosaveSnapshot | null | undefined>
): SettingsAutosaveSnapshot {
  const presentStates = states.filter(
    (state): state is SettingsAutosaveSnapshot =>
      state !== null && state !== undefined,
  );

  if (presentStates.length === 0) {
    return { status: "saved" };
  }

  return presentStates.reduce((strongest, candidate) => {
    return AUTOSAVE_STATE_PRIORITY[candidate.status] >
      AUTOSAVE_STATE_PRIORITY[strongest.status]
      ? candidate
      : strongest;
  });
}

export function getSettingsSectionMatchScores({
  searchQuery,
  isAdmin,
}: {
  searchQuery: string;
  isAdmin: boolean;
}): SettingsSectionMatchScores | null {
  if (!searchQuery) {
    return null;
  }

  return {
    personal: getMatchScore(searchQuery, TAB_KEYWORDS.personal),
    ai: getMatchScore(searchQuery, TAB_KEYWORDS.ai),
    companion: getMatchScore(searchQuery, TAB_KEYWORDS.companion),
    administration: isAdmin
      ? getMatchScore(searchQuery, TAB_KEYWORDS.administration)
      : 1,
    updates: getMatchScore(searchQuery, TAB_KEYWORDS.updates),
    help: getMatchScore(searchQuery, TAB_KEYWORDS.help),
  };
}

export function getPreferredSettingsSectionForSearch({
  activeSectionId,
  matchScores,
}: {
  activeSectionId: SettingsSectionId;
  matchScores: SettingsSectionMatchScores | null;
}): SettingsSectionId | null {
  if (!matchScores) {
    return null;
  }

  const currentScore = matchScores[activeSectionId];
  let bestSectionId: SettingsSectionId | null = null;
  let bestScore = 1.0;

  (Object.entries(matchScores) as [SettingsSectionId, number][]).forEach(
    ([sectionId, score]) => {
      if (score < bestScore) {
        bestScore = score;
        bestSectionId = sectionId;
      }
    },
  );

  if (!bestSectionId || bestSectionId === activeSectionId) {
    return null;
  }

  if (bestScore === 0 && currentScore > 0) {
    return bestSectionId;
  }

  if (currentScore >= 0.8 && bestScore < 0.6) {
    return bestSectionId;
  }

  return null;
}