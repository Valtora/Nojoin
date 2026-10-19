import { Settings } from "@/types";
import { Switch } from "@/components/ui/Switch";
import SettingsCard from "./SettingsCard";
import SettingsRow from "./SettingsRow";
import { SETTINGS_CONTROL_END } from "./settingsControls";

interface AiAutomaticEnhancementSectionProps {
  settings: Settings;
  /** Apply and save immediately (the switches are discrete controls). */
  onPersist: (newSettings: Settings) => void;
}

/** What the AI does once a meeting has been processed (per user). */
export default function AiAutomaticEnhancementSection({
  settings,
  onPersist,
}: AiAutomaticEnhancementSectionProps) {
  return (
    <SettingsCard
      id="notes-after-meeting"
      title="After the meeting"
      description="What the AI does once a meeting has been processed."
    >
      <SettingsRow
        id="notes-short-titles"
        label="Short titles"
        labelId="notes-short-titles-label"
        descriptionId="notes-short-titles-help"
        description="Name meetings in three to five words rather than a longer description."
        controlClassName={SETTINGS_CONTROL_END}
      >
        <Switch
          checked={settings.prefer_short_titles !== false}
          aria-labelledby="notes-short-titles-label"
          aria-describedby="notes-short-titles-help"
          onCheckedChange={(checked) =>
            onPersist({ ...settings, prefer_short_titles: checked })
          }
        />
      </SettingsRow>

      <SettingsRow
        id="notes-auto-analyse"
        label="Analyse meetings automatically"
        labelId="notes-auto-analyse-label"
        descriptionId="notes-auto-analyse-help"
        description="Run the AI analysis of each meeting as soon as it is processed, within any daily limit your administrator has set. Each run uses your AI provider quota, so it is off until you turn it on."
        controlClassName={SETTINGS_CONTROL_END}
      >
        <Switch
          checked={settings.auto_analyse_meetings === true}
          aria-labelledby="notes-auto-analyse-label"
          aria-describedby="notes-auto-analyse-help"
          onCheckedChange={(checked) =>
            onPersist({ ...settings, auto_analyse_meetings: checked })
          }
        />
      </SettingsRow>
    </SettingsCard>
  );
}
