"use client";

import CalendarConnectionsSettings from "@/components/settings/CalendarConnectionsSettings";
import CalendarProviderSettings from "@/components/settings/CalendarProviderSettings";
import ConnectedAppsSettings from "@/components/settings/ConnectedAppsSettings";
import SettingsAdvancedSection from "@/components/settings/SettingsAdvancedSection";
import SettingsCategoryLayout from "@/components/settings/SettingsCategoryLayout";
import { useSettingsContext } from "@/components/settings/SettingsProvider";

export default function IntegrationsSettingsPage() {
  const { isAdmin } = useSettingsContext();

  return (
    <SettingsCategoryLayout categoryId="integrations">
      <CalendarConnectionsSettings />
      <ConnectedAppsSettings />

      {isAdmin && (
        <SettingsAdvancedSection categoryId="integrations">
          <CalendarProviderSettings />
        </SettingsAdvancedSection>
      )}
    </SettingsCategoryLayout>
  );
}
