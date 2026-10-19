import { useEffect, useState } from "react";

import { LanguageRegistry, Settings } from "@/types";
import { getLanguageOptions } from "@/lib/api";
import SettingsCard from "./SettingsCard";
import SettingsRow from "./SettingsRow";
import { SETTINGS_INPUT_CLASS, SETTINGS_SELECT_CLASS } from "./settingsControls";

interface NotesLanguageSectionProps {
  settings: Settings;
  /** Debounced apply (the free-text custom instruction). */
  onUpdate: (newSettings: Settings) => void;
  /** Apply and save immediately (the language select). */
  onPersist: (newSettings: Settings) => void;
}

/** The language generated titles and notes are written in (per user). */
export default function NotesLanguageSection({
  settings,
  onUpdate,
  onPersist,
}: NotesLanguageSectionProps) {
  const [languageRegistry, setLanguageRegistry] =
    useState<LanguageRegistry | null>(null);
  const language = settings.notes_language || "english";

  useEffect(() => {
    getLanguageOptions().then(setLanguageRegistry).catch(console.error);
  }, []);

  return (
    <SettingsCard
      id="notes-language"
      title="Language"
      description="The language titles and notes are written in. Saved notes are not translated; generate them again to change their language."
    >
      <SettingsRow
        label="Notes language"
        controlId="notes-language-select"
        description="Covers titles, headings, summaries, notes and action items."
      >
        <select
          id="notes-language-select"
          aria-describedby="notes-language-select-help"
          value={language}
          onChange={(event) =>
            onPersist({ ...settings, notes_language: event.target.value })
          }
          disabled={!languageRegistry}
          className={SETTINGS_SELECT_CLASS}
        >
          {!languageRegistry && <option value="english">Loading languages...</option>}
          {languageRegistry?.notes_languages.map((option) => (
            <option key={option.code} value={option.code}>
              {option.label}
            </option>
          ))}
        </select>
      </SettingsRow>

      {language === "custom" && (
        <SettingsRow
          label="Your instruction"
          controlId="notes-language-custom"
          description="Needed for Custom. Set the language, regional conventions, tone and heading style."
        >
          <input
            id="notes-language-custom"
            aria-describedby="notes-language-custom-help"
            type="text"
            value={settings.notes_language_custom_instruction || ""}
            onChange={(event) =>
              onUpdate({
                ...settings,
                notes_language_custom_instruction: event.target.value,
              })
            }
            maxLength={languageRegistry?.custom_instruction_max_length || 300}
            placeholder="Formal Canadian French, short headings"
            className={SETTINGS_INPUT_CLASS}
          />
        </SettingsRow>
      )}
    </SettingsCard>
  );
}
