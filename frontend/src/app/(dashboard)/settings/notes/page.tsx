"use client";

import AiAutomaticEnhancementSection from "@/components/settings/AiAutomaticEnhancementSection";
import MeetingEdgeSection from "@/components/settings/MeetingEdgeSection";
import NotesTemplatesSection from "@/components/settings/NotesTemplatesSection";
import SettingsAdvancedSection from "@/components/settings/SettingsAdvancedSection";
import SettingsCategoryLayout from "@/components/settings/SettingsCategoryLayout";
import { useSettingsContext } from "@/components/settings/SettingsProvider";
import { useAISettingsModels } from "@/components/settings/useAISettingsModels";

/**
 * Meeting Edge is AI during a meeting; notes and enhancement are AI after one.
 * One page covers what the AI produces for you, rather than splitting it by
 * when it runs.
 */
export default function NotesSettingsPage() {
  const { settings, updateSetting, persistNow, isAdmin } = useSettingsContext();
  const models = useAISettingsModels({ settings, onPersist: persistNow });

  return (
    <SettingsCategoryLayout categoryId="notes">
      <NotesTemplatesSection
        settings={settings}
        onPersist={persistNow}
        isAdmin={isAdmin}
      />

      <MeetingEdgeSection
        settings={settings}
        onUpdate={updateSetting}
        onPersist={persistNow}
        isAdmin={isAdmin}
        models={models}
      />

      <SettingsAdvancedSection categoryId="notes">
        <AiAutomaticEnhancementSection
          settings={settings}
          onPersist={persistNow}
        />
      </SettingsAdvancedSection>
    </SettingsCategoryLayout>
  );
}
