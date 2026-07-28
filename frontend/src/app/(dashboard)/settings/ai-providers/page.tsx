"use client";

import AiHuggingFaceSection from "@/components/settings/AiHuggingFaceSection";
import AiModelDependenciesSection from "@/components/settings/AiModelDependenciesSection";
import CliUsageTab from "@/components/settings/CliUsageTab";
import SecondaryProviderSection from "@/components/settings/SecondaryProviderSection";
import ServerProviderSection from "@/components/settings/ServerProviderSection";
import SettingsAdvancedSection from "@/components/settings/SettingsAdvancedSection";
import SettingsCategoryLayout from "@/components/settings/SettingsCategoryLayout";
import { useSettingsContext } from "@/components/settings/SettingsProvider";
import { useAISettingsModels } from "@/components/settings/useAISettingsModels";

export default function AiProvidersSettingsPage() {
  const { settings, updateSetting, persistNow, isAdmin } = useSettingsContext();
  const models = useAISettingsModels({ settings, onPersist: persistNow });

  return (
    <SettingsCategoryLayout categoryId="ai-providers">
      <ServerProviderSection
        settings={settings}
        onUpdate={updateSetting}
        onPersist={persistNow}
        models={models}
      />

      <CliUsageTab />

      <SettingsAdvancedSection categoryId="ai-providers">
        <SecondaryProviderSection
          settings={settings}
          onUpdate={updateSetting}
          onPersist={persistNow}
          models={models}
        />

        <AiHuggingFaceSection settings={settings} />

        <AiModelDependenciesSection
          modelStatus={models.modelStatus}
          deleting={models.deleting}
          handleDeleteModel={models.handleDeleteModel}
          isAdmin={isAdmin}
          downloadProgress={models.downloadProgress}
          preparationRunning={models.preparationRunning}
          startPreparation={models.startPreparation}
        />
      </SettingsAdvancedSection>
    </SettingsCategoryLayout>
  );
}
