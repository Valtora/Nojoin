"use client";

import GeneralSettings from "@/components/settings/GeneralSettings";
import SettingsCategoryLayout from "@/components/settings/SettingsCategoryLayout";
import { useSettingsContext } from "@/components/settings/SettingsProvider";

export default function AppearanceSettingsPage() {
  const { settings, updateSetting } = useSettingsContext();

  return (
    <SettingsCategoryLayout categoryId="appearance">
      <GeneralSettings
        settings={settings}
        onUpdate={updateSetting}
        suppressNoMatch
        sections={["appearance", "dateTime", "spellcheck"]}
      />
    </SettingsCategoryLayout>
  );
}
