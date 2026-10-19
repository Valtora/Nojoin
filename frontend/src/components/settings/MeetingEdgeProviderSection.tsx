import { Settings } from "@/types";
import { Switch } from "@/components/ui/Switch";
import SettingsCard from "./SettingsCard";
import SettingsRow from "./SettingsRow";
import type { AISettingsModels } from "./useAISettingsModels";
import { ModelPicker } from "./aiProviderControls";
import { SETTINGS_CONTROL_COLUMN_WIDE } from "./settingsControls";
import {
  getModelOptionsForProvider,
  getSelectedModelForProvider,
  withSelectedModelForProvider,
} from "./aiSettingsModels";

interface MeetingEdgeProviderSectionProps {
  settings: Settings;
  /** Apply and save immediately (the switch and the model select). */
  onPersist: (newSettings: Settings) => void;
  models: AISettingsModels;
}

/**
 * Meeting Edge for the whole installation: whether it runs, and which of the
 * default provider's models answers it. Each person's own preference, how
 * readily it explains terms, stays under Notes and live assistance.
 */
export default function MeetingEdgeProviderSection({
  settings,
  onPersist,
  models,
}: MeetingEdgeProviderSectionProps) {
  const enabled = settings.enable_meeting_edge !== false;
  const options = getModelOptionsForProvider(
    settings,
    models.availableModels,
    "live",
  );

  return (
    <SettingsCard
      id="ai-providers-meeting-edge"
      title="Meeting Edge"
      description="Live guidance during a recording: questions to ask, points missed, and terms explained."
    >
      <SettingsRow
        label="Meeting Edge"
        labelId="ai-meeting-edge-label"
        descriptionId="ai-meeting-edge-help"
        description="Turns live guidance, and the model calls behind it, on for everyone on this installation."
      >
        <div className="flex @min-[26rem]:justify-end">
          <Switch
            checked={enabled}
            aria-labelledby="ai-meeting-edge-label"
            aria-describedby="ai-meeting-edge-help"
            onCheckedChange={(checked) =>
              onPersist({ ...settings, enable_meeting_edge: checked })
            }
          />
        </div>
      </SettingsRow>

      <SettingsRow
        id="ai-providers-meeting-edge-model"
        label="Meeting Edge model"
        controlId="ai-meeting-edge-model"
        controlClassName={SETTINGS_CONTROL_COLUMN_WIDE}
        description="A faster model than the default keeps guidance responsive."
      >
        <ModelPicker
          id="ai-meeting-edge-model"
          value={getSelectedModelForProvider(settings, "live")}
          options={options}
          placeholder="Same as the default model"
          allowEmpty
          disabled={!enabled || options.length === 0}
          onChange={(value) =>
            onPersist(withSelectedModelForProvider(settings, "live", value))
          }
        />
      </SettingsRow>
    </SettingsCard>
  );
}
