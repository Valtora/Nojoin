import type { Settings } from "@/types";
import { TuningInput } from "./ProcessingTuningSettings";
import SettingsCard from "./SettingsCard";
import SettingsRow from "./SettingsRow";

interface Props {
  settings: Settings;
  onUpdate: (settings: Settings) => void;
}

export default function LineBreakPauseSettings({ settings, onUpdate }: Props) {
  return (
    <SettingsCard title="Transcript lines">
      <SettingsRow
        id="transcription-line-break-pause"
        label="Line break pause"
        descriptionId="line-break-pause-help"
        description="Start a new line after a pause longer than this many seconds. Applies to live lines when word settling is on, and to processed transcripts when speakers are detected. Empty uses the installation's value, or 1 second. A change applies to new live lines and to recordings processed afterwards; saved transcripts stay as they are."
      >
        <TuningInput
          tuningKey="line_break_pause_s"
          value={settings.line_break_pause_s}
          descriptionId="line-break-pause-help"
          onChange={(key, value) => onUpdate({ ...settings, [key]: value })}
        />
      </SettingsRow>
    </SettingsCard>
  );
}
