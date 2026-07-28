"use client";

import HelpSettings from "@/components/settings/HelpSettings";
import SettingsCategoryLayout from "@/components/settings/SettingsCategoryLayout";
import { useSettingsContext } from "@/components/settings/SettingsProvider";

export default function HelpSettingsPage() {
  const { userId } = useSettingsContext();

  return (
    <SettingsCategoryLayout categoryId="help">
      <HelpSettings userId={userId} />
    </SettingsCategoryLayout>
  );
}
