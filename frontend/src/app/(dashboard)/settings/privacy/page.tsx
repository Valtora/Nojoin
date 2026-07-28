"use client";

import SettingsCategoryLayout from "@/components/settings/SettingsCategoryLayout";
import TelemetrySection from "@/components/settings/TelemetrySection";

export default function PrivacySettingsPage() {
  return (
    <SettingsCategoryLayout categoryId="privacy">
      <TelemetrySection />
    </SettingsCategoryLayout>
  );
}
