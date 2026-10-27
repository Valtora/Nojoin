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
        description="Start a new line after a pause longer than this many seconds. Applies to live settling lines and diarised final transcripts when speakers are detected. Without speaker detection, this pause does not regroup the transcription engine's segments. Empty uses the installation's value, or 1 second. Changes apply when recordings are processed; saved transcripts stay as they are."
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
