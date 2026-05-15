import { fuzzyMatch } from "@/lib/searchUtils";
import type { Settings } from "@/types";

import type { SettingsAutosaveSnapshot } from "./SettingsAutosaveState";
import AccountSettings from "./AccountSettings";
import GeneralSettings from "./GeneralSettings";
import { TAB_KEYWORDS } from "./keywords";
import SettingsCallout from "./SettingsCallout";

interface PersonalSettingsProps {
  settings: Settings;
  onUpdate: (newSettings: Settings) => void;
  searchQuery?: string;
  forcePasswordChange?: boolean;
  initialUsername: string | null;
  onUsernameSaved?: (username: string) => void;
  onAutosaveStateChange?: (snapshot: SettingsAutosaveSnapshot) => void;
}

export default function PersonalSettings({
  settings,
  onUpdate,
  searchQuery = "",
  forcePasswordChange = false,
  initialUsername,
  onUsernameSaved,
  onAutosaveStateChange,
}: PersonalSettingsProps) {
  const personalKeywords = forcePasswordChange
    ? TAB_KEYWORDS.account
    : TAB_KEYWORDS.personal;

  const showAnything = !searchQuery || fuzzyMatch(searchQuery, personalKeywords);

  if (!showAnything) {
    return (
      <SettingsCallout
        tone="neutral"
        title="No matching settings"
        message="Try a broader search term for profile, passwords, calendars, appearance, timezone, spellcheck, or recording preferences."
      />
    );
  }

  return (
    <div className="space-y-8">
      <AccountSettings
        forcePasswordChange={forcePasswordChange}
        initialUsername={initialUsername}
        onUsernameSaved={onUsernameSaved}
        onAutosaveStateChange={onAutosaveStateChange}
        searchQuery={searchQuery}
        suppressNoMatch
        includeCalendarConnections={!forcePasswordChange}
      />
      {!forcePasswordChange && (
        <GeneralSettings
          settings={settings}
          onUpdate={onUpdate}
          searchQuery={searchQuery}
          suppressNoMatch
        />
      )}
    </div>
  );
}