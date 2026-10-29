import { Settings } from "@/types";
import { Switch } from "@/components/ui/Switch";
import { SelectBase } from "@/components/ui/nativeBases";
import SettingsCard from "./SettingsCard";
import SettingsRow from "./SettingsRow";
import { SETTINGS_CONTROL_END, SETTINGS_SELECT_CLASS } from "./settingsControls";

interface AiAutomaticEnhancementSectionProps {
  settings: Settings;
  /** Apply and save immediately (the switches are discrete controls). */
  onPersist: (newSettings: Settings) => void;
}

/** Automatic meeting naming and analysis (per user). */
export default function AiAutomaticEnhancementSection({
  settings,
  onPersist,
}: AiAutomaticEnhancementSectionProps) {
  const titleDelay = settings.live_title_delay_minutes ?? 0;
  const titleDelays = [...new Set([0, 1, 2, 5, 10, 15, 30, 60, 120, titleDelay])].sort((a, b) => a - b);

  return (
    <SettingsCard
      id="notes-after-meeting"
      title="Automatic enhancement"
      description="When the AI names your meetings and analyses them."
    >
      <SettingsRow
        id="notes-live-title-delay"
        label="Live title delay"
        controlId="notes-live-title-delay-select"
        description="Name a meeting after processing, or once this much live transcript is available. Renaming it yourself keeps your chosen title."
      >
        <SelectBase
          data-ui-fill="control"
          data-ui-disabled-fill="control-disabled"
          data-ui-border="full"
          id="notes-live-title-delay-select"
          aria-describedby="notes-live-title-delay-select-help"
          value={titleDelay}
          className={SETTINGS_SELECT_CLASS}
          onChange={(event) => onPersist({ ...settings, live_title_delay_minutes: Number(event.target.value) })}
        >
          {titleDelays.map((minutes) => (
            <option key={minutes} value={minutes}>
              {minutes === 0 ? "After the meeting" : `After ${minutes} ${minutes === 1 ? "minute" : "minutes"}`}
            </option>
          ))}
        </SelectBase>
      </SettingsRow>

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
