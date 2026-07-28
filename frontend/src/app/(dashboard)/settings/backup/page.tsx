"use client";

import BackupRestore from "@/components/settings/BackupRestore";
import SettingsCategoryLayout from "@/components/settings/SettingsCategoryLayout";

export default function BackupSettingsPage() {
  return (
    <SettingsCategoryLayout categoryId="backup">
      <BackupRestore />
    </SettingsCategoryLayout>
  );
}
