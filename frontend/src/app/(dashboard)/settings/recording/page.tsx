"use client";

import CaptureProcessingSettings from "@/components/settings/CaptureProcessingSettings";
import CaptureSettings from "@/components/settings/CaptureSettings";
import GeneralSettings from "@/components/settings/GeneralSettings";
import SettingsAdvancedSection from "@/components/settings/SettingsAdvancedSection";
import SettingsCategoryLayout from "@/components/settings/SettingsCategoryLayout";
import { useSettingsContext } from "@/components/settings/SettingsProvider";

/**
 * Recording covers the whole path from microphone to processed audio. The
 * processing defaults (voice activity detection, speaker diarization) used to
 * sit under Personal, three clicks from the gain sliders that explain the same
 * symptoms.
 */
export default function RecordingSettingsPage() {
  const { settings, updateSetting } = useSettingsContext();

  return (
    <SettingsCategoryLayout categoryId="recording">
      <CaptureSettings />

      <SettingsAdvancedSection categoryId="recording">
        <CaptureProcessingSettings />

        <GeneralSettings
          settings={settings}
          onUpdate={updateSetting}
          suppressNoMatch
          sections={["processing"]}
        />
      </SettingsAdvancedSection>
    </SettingsCategoryLayout>
  );
}
