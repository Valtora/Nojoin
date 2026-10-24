"use client";

import AiAutomaticEnhancementSection from "@/components/settings/AiAutomaticEnhancementSection";
import SpeakerNamesSection from "@/components/settings/SpeakerNamesSection";
import MeetingEdgeSection from "@/components/settings/MeetingEdgeSection";
import NotesLanguageSection from "@/components/settings/NotesLanguageSection";
import NotesTemplatesSection from "@/components/settings/NotesTemplatesSection";
import SettingsCategoryLayout from "@/components/settings/SettingsCategoryLayout";
import { useSettingsContext } from "@/components/settings/SettingsProvider";

/**
 * Everything the AI writes for you: the notes and their language, what happens
 * once a meeting is processed, how its speakers' suggested names are settled,
 * and how Meeting Edge explains terms while it runs. Whether Meeting Edge runs, and on which model, is under AI providers.
 */
export default function NotesSettingsPage() {
  const { settings, updateSetting, persistNow, isAdmin } = useSettingsContext();

  return (
    <SettingsCategoryLayout categoryId="notes">
      <NotesTemplatesSection
        settings={settings}
        onPersist={persistNow}
        isAdmin={isAdmin}
      />

      <NotesLanguageSection
        settings={settings}
        onUpdate={updateSetting}
        onPersist={persistNow}
      />

      <AiAutomaticEnhancementSection settings={settings} onPersist={persistNow} />

      <SpeakerNamesSection settings={settings} onPersist={persistNow} />

      <MeetingEdgeSection
        settings={settings}
        onUpdate={updateSetting}
        isAdmin={isAdmin}
      />
    </SettingsCategoryLayout>
  );
}
