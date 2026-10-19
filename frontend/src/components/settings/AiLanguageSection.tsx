import { LanguageRegistry, Settings } from "@/types";
import SettingsCard from "./SettingsCard";
import SettingsRow from "./SettingsRow";
import { SETTINGS_SELECT_CLASS } from "./settingsControls";

interface AiLanguageSectionProps {
  settings: Settings;
  /** Apply and save immediately (the language select). */
  onPersist: (newSettings: Settings) => void;
  languageRegistry: LanguageRegistry | null;
}

/**
 * The language spoken in recordings (per user). The language notes are written
 * in is a notes setting and lives under Notes and live assistance.
 */
export default function AiLanguageSection({
  settings,
  onPersist,
  languageRegistry,
}: AiLanguageSectionProps) {
  const backend: keyof LanguageRegistry["engine_capabilities"] =
    settings.transcription_backend === "canary"
      ? "canary"
      : settings.transcription_backend === "parakeet"
        ? "parakeet"
        : "whisper";
  const capability = languageRegistry?.engine_capabilities[backend];

  return (
    <SettingsCard
      id="transcription-language"
      title="Language"
      description="The language spoken in your recordings."
    >
      <SettingsRow
        label="Transcription language"
        controlId="transcription-language-select"
        description={capability?.guidance || "Loading what this engine supports..."}
      >
        <select
          id="transcription-language-select"
          aria-describedby="transcription-language-select-help"
          value={settings.transcription_language || "auto"}
          onChange={(event) =>
            onPersist({ ...settings, transcription_language: event.target.value })
          }
          disabled={!languageRegistry || capability?.forced_language === false}
          className={SETTINGS_SELECT_CLASS}
        >
          {!languageRegistry && <option value="auto">Loading languages...</option>}
          {languageRegistry?.transcription_languages.map((language) => (
            <option key={language.code} value={language.code}>
              {language.label}
            </option>
          ))}
        </select>
      </SettingsRow>
    </SettingsCard>
  );
}
