"use client";

import AiAnalysisLimitSection from "@/components/settings/AiAnalysisLimitSection";
import InvitesTab from "@/components/settings/InvitesTab";
import SettingsCategoryLayout from "@/components/settings/SettingsCategoryLayout";
import UsersTab from "@/components/settings/UsersTab";

export default function UsersSettingsPage() {
  return (
    <SettingsCategoryLayout categoryId="users">
      <UsersTab />
      <InvitesTab />
      <AiAnalysisLimitSection />
    </SettingsCategoryLayout>
  );
}
