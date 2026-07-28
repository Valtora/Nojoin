"use client";

import SettingsCategoryLayout from "@/components/settings/SettingsCategoryLayout";
import UpdatesSettings from "@/components/settings/UpdatesSettings";

export default function UpdatesSettingsPage() {
  return (
    <SettingsCategoryLayout categoryId="updates">
      <UpdatesSettings />
    </SettingsCategoryLayout>
  );
}
