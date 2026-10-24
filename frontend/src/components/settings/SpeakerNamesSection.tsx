import { Settings } from "@/types";
import { Switch } from "@/components/ui/Switch";
import SettingsCard from "./SettingsCard";
import SettingsRow from "./SettingsRow";
import { SETTINGS_CONTROL_END } from "./settingsControls";

interface SpeakerNamesSectionProps {
  settings: Settings;
  /** Apply and save immediately (the switches are discrete controls). */
  onPersist: (newSettings: Settings) => void;
}

/** How suggested speaker names are settled, and what voiceprints learn from (per user). */
export default function SpeakerNamesSection({ settings, onPersist }: SpeakerNamesSectionProps) {
  return (
    <SettingsCard
      id="notes-speaker-names"
      title="Speaker names"
      description="Names suggested for a meeting's speakers by the AI, a self-introduction or a voice match, and what People voiceprints learn from."
    >
      <SettingsRow
        id="notes-confirm-suggested-names"
        label="Confirm suggested names automatically"
        labelId="notes-confirm-suggested-names-label"
        descriptionId="notes-confirm-suggested-names-help"
        description="Take suggested names as given instead of asking you to confirm each one. Applies to meetings processed from now on; names already waiting stay as they are. Off by default."
        controlClassName={SETTINGS_CONTROL_END}
      >
        <Switch
          checked={settings.confirm_suggested_names === true}
          aria-labelledby="notes-confirm-suggested-names-label"
          aria-describedby="notes-confirm-suggested-names-help"
          onCheckedChange={(checked) =>
            onPersist({ ...settings, confirm_suggested_names: checked })
          }
        />
      </SettingsRow>

      <SettingsRow
        id="notes-voiceprint-learning"
        label="Learn voiceprints from unconfirmed names"
        labelId="notes-voiceprint-learning-label"
        descriptionId="notes-voiceprint-learning-help"
        description="Let a voice match, or a name taken as given, update the person's voiceprint before you confirm it. Off by default: voiceprints learn only from names you give or confirm."
        controlClassName={SETTINGS_CONTROL_END}
      >
        <Switch
          checked={settings.voiceprint_learning === "automatic"}
          aria-labelledby="notes-voiceprint-learning-label"
          aria-describedby="notes-voiceprint-learning-help"
          onCheckedChange={(checked) =>
            onPersist({
              ...settings,
              voiceprint_learning: checked ? "automatic" : "confirmed",
            })
          }
        />
      </SettingsRow>
    </SettingsCard>
  );
}
