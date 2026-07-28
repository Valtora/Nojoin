"use client";

import AiLanguageSection from "@/components/settings/AiLanguageSection";
import AiTranscriptionSection from "@/components/settings/AiTranscriptionSection";
import GlossarySection from "@/components/settings/GlossarySection";
import SettingsAdvancedSection from "@/components/settings/SettingsAdvancedSection";
import SettingsCategoryLayout from "@/components/settings/SettingsCategoryLayout";
import { useSettingsContext } from "@/components/settings/SettingsProvider";
import { useAISettingsModels } from "@/components/settings/useAISettingsModels";

export default function TranscriptionSettingsPage() {
  const { settings, updateSetting, persistNow, isAdmin } = useSettingsContext();
  const models = useAISettingsModels({ settings, onPersist: persistNow });

  return (
    <SettingsCategoryLayout categoryId="transcription">
      <AiLanguageSection
        settings={settings}
        onUpdate={updateSetting}
        onPersist={persistNow}
        languageRegistry={models.languageRegistry}
      />

      <GlossarySection
        settings={settings}
        onUpdate={updateSetting}
        isAdmin={isAdmin}
      />

      {isAdmin && (
        <SettingsAdvancedSection categoryId="transcription">
          <AiTranscriptionSection
            settings={settings}
            onPersist={persistNow}
            isAdmin={isAdmin}
            models={models}
          />
        </SettingsAdvancedSection>
      )}
    </SettingsCategoryLayout>
  );
}
