"use client";

import AiRoutingSection from "@/components/settings/AiRoutingSection";
import SettingsCategoryLayout from "@/components/settings/SettingsCategoryLayout";
import { useSettingsContext } from "@/components/settings/SettingsProvider";

/**
 * Per-user AI: the routing choice and the subscription connect flow. Split out
 * from provider configuration because every user needs these, while the
 * provider, models and credentials behind them are install-wide and admin-only.
 */
export default function YourAiSettingsPage() {
  const { settings, persistNow, isAdmin } = useSettingsContext();

  return (
    <SettingsCategoryLayout categoryId="your-ai">
      <AiRoutingSection
        settings={settings}
        onPersist={persistNow}
        isAdmin={isAdmin}
      />
    </SettingsCategoryLayout>
  );
}
