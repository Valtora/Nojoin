"use client";

import AccountSettings from "@/components/settings/AccountSettings";
import SettingsCategoryLayout from "@/components/settings/SettingsCategoryLayout";
import { useSettingsContext } from "@/components/settings/SettingsProvider";
import SettingsCallout from "@/components/settings/SettingsCallout";

export default function ProfileSettingsPage() {
  const {
    username,
    setUsername,
    forcePasswordChange,
    setAccountAutosaveState,
  } = useSettingsContext();

  return (
    <SettingsCategoryLayout categoryId="profile">
      {forcePasswordChange && (
        <SettingsCallout
          tone="warning"
          title="Password change required"
          message="Your account must change its password before Nojoin will allow access to other authenticated features."
        />
      )}

      <AccountSettings
        forcePasswordChange={forcePasswordChange}
        initialUsername={username}
        onUsernameSaved={setUsername}
        onAutosaveStateChange={setAccountAutosaveState}
        includeCalendarConnections={false}
        suppressNoMatch
      />
    </SettingsCategoryLayout>
  );
}
