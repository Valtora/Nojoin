import { SelectBase } from "@/components/ui/nativeBases";
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
  // An engine that can't detect (Canary) transcribes "auto" as English, so show
  // that. The stored "auto" is kept: switching back to Whisper detects again.
  const canAutoDetect = capability?.auto_detect !== false;
  const stored = settings.transcription_language || "auto";
  const shown = !canAutoDetect && stored === "auto" ? "en" : stored;
  const languages = languageRegistry?.transcription_languages.filter(
    (language) => canAutoDetect || language.code !== "auto",
  );

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
        <SelectBase
          data-ui-fill="control"
          data-ui-disabled-fill="control-disabled"
          data-ui-border="full"
          id="transcription-language-select"
          aria-describedby="transcription-language-select-help"
          value={shown}
          onChange={(event) =>
            onPersist({ ...settings, transcription_language: event.target.value })
          }
          disabled={!languageRegistry || capability?.forced_language === false}
          className={SETTINGS_SELECT_CLASS}
        >
          {!languageRegistry && <option value="auto">Loading languages...</option>}
          {languages?.map((language) => (
            <option key={language.code} value={language.code}>
              {language.label}
            </option>
          ))}
        </SelectBase>
      </SettingsRow>
    </SettingsCard>
  );
}
