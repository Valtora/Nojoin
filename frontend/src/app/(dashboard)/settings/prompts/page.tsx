"use client";

import SettingsCategoryLayout from "@/components/settings/SettingsCategoryLayout";
import PromptsSettings from "@/components/settings/PromptsSettings";

export default function PromptsSettingsPage() {
  return <SettingsCategoryLayout categoryId="prompts"><PromptsSettings /></SettingsCategoryLayout>;
}
