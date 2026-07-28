"use client";

import SettingsCategoryLayout from "@/components/settings/SettingsCategoryLayout";
import SystemTab from "@/components/settings/SystemTab";

export default function SystemSettingsPage() {
  return (
    <SettingsCategoryLayout categoryId="system">
      <SystemTab />
    </SettingsCategoryLayout>
  );
}
