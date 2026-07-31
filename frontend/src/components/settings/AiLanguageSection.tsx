import { LanguageRegistry, Settings } from "@/types";
import SettingsBlock from "./SettingsBlock";
import SettingsCard from "./SettingsCard";

interface AiLanguageSectionProps {
  settings: Settings;
  /** Debounced apply (the free-text custom instruction). */
  onUpdate: (newSettings: Settings) => void;
  /** Apply and save immediately (the language selects). */
  onPersist: (newSettings: Settings) => void;
  languageRegistry: LanguageRegistry | null;
}

/** "Language preferences" AI section (per-user). */
export default function AiLanguageSection({
  settings,
  onUpdate,
  onPersist,
  languageRegistry,
}: AiLanguageSectionProps) {
  const selectedTranscriptionBackend: keyof LanguageRegistry["engine_capabilities"] =
    settings.transcription_backend === "canary"
      ? "canary"
      : settings.transcription_backend === "parakeet"
        ? "parakeet"
        : "whisper";
  const transcriptionLanguageCapability =
    languageRegistry?.engine_capabilities[selectedTranscriptionBackend];

  return (
    <SettingsCard
      title="Language preferences"
      description="Choose the source language used for transcription and the language used for generated meeting titles and notes."
    >
      <SettingsBlock className="space-y-6">
        <div>
          <label className="block text-sm font-medium text-contrast-muted mb-2">
            Transcription language
          </label>
          <select
            value={settings.transcription_language || "auto"}
            onChange={(event) =>
              onPersist({
                ...settings,
                transcription_language: event.target.value,
              })
            }
            disabled={
              !languageRegistry ||
              transcriptionLanguageCapability?.forced_language === false
            }
            className="w-full p-2.5 rounded-lg border border-control-border bg-surface-inset text-foreground focus:ring-2 focus:ring-action outline-none transition-all disabled:opacity-60"
          >
            {!languageRegistry && (
              <option value="auto">Loading languages...</option>
            )}
            {languageRegistry?.transcription_languages.map((language) => (
              <option key={language.code} value={language.code}>
                {language.label}
              </option>
            ))}
          </select>
          <p className="mt-2 text-xs contrast-helper">
            {transcriptionLanguageCapability?.guidance ||
              "Language capabilities are loading."}
          </p>
        </div>

        <div>
          <label className="block text-sm font-medium text-contrast-muted mb-2">
            Notes generation language
          </label>
          <select
            value={settings.notes_language || "english"}
            onChange={(event) =>
              onPersist({
                ...settings,
                notes_language: event.target.value,
              })
            }
            disabled={!languageRegistry}
            className="w-full p-2.5 rounded-lg border border-control-border bg-surface-inset text-foreground focus:ring-2 focus:ring-action outline-none transition-all disabled:opacity-60"
          >
            {!languageRegistry && (
              <option value="english">Loading languages...</option>
            )}
            {languageRegistry?.notes_languages.map((language) => (
              <option key={language.code} value={language.code}>
                {language.label}
              </option>
            ))}
          </select>
          <p className="mt-2 text-xs contrast-helper">
            This controls generated titles, headings, summaries, detailed notes,
            and action items. JSON field names remain stable.
          </p>
        </div>

        {(settings.notes_language || "english") === "custom" && (
          <div>
            <label className="block text-sm font-medium text-contrast-muted mb-2">
              Custom notes language or style instruction
            </label>
            <input
              type="text"
              value={settings.notes_language_custom_instruction || ""}
              onChange={(event) =>
                onUpdate({
                  ...settings,
                  notes_language_custom_instruction: event.target.value,
                })
              }
              maxLength={languageRegistry?.custom_instruction_max_length || 300}
              placeholder="e.g. Formal Canadian French with concise executive-style headings"
              className="w-full p-3 rounded-lg border border-control-border bg-surface-inset text-foreground focus:ring-2 focus:ring-action outline-none transition-all"
            />
            <p className="mt-2 text-xs contrast-helper">
              Required for Custom. This can control language, regional
              conventions, tone, and heading style.
            </p>
          </div>
        )}
      </SettingsBlock>
    </SettingsCard>
  );
}
