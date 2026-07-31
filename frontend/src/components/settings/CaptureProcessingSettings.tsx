"use client";

import { Mic } from "lucide-react";

import { useAudioWarningStore } from "@/lib/audioWarningStore";
import { useCapture } from "@/lib/capture/CaptureProvider";
import { useNotificationStore } from "@/lib/notificationStore";
import { Switch } from "@/components/ui/Switch";

import SettingsBlock from "./SettingsBlock";
import SettingsCallout from "./SettingsCallout";
import SettingsCard from "./SettingsCard";
import SettingsRow from "./SettingsRow";
import { SETTINGS_BUTTON_SECONDARY } from "./settingsControls";

/**
 * The browser-side audio processing toggles and the quiet-audio reminder reset.
 *
 * Split out of CaptureSettings because these sit behind the Advanced gate while
 * device selection and levels do not: all three ship enabled, suit almost
 * everyone, and are touched only when something sounds wrong.
 */
export default function CaptureProcessingSettings() {
  const { addNotification } = useNotificationStore();
  const { settings, updateSettings } = useCapture();
  const suppressQuietAudioWarnings = useAudioWarningStore(
    (state) => state.suppressQuietAudioWarnings,
  );
  const resetWarnings = useAudioWarningStore((state) => state.resetWarnings);

  const toggleControlClass = "sm:min-w-0 sm:flex sm:justify-end";

  return (
    <>
      <SettingsCard
        title="Browser Processing"
        description="What the browser does to microphone audio before Nojoin receives it."
      >
        <SettingsBlock>
          <SettingsCallout
            tone="info"
            message="These toggles take effect on the next input test, and on the next recording start or resume. Gain changes apply immediately."
          />
        </SettingsBlock>

        <SettingsRow
          id="recording-echo-cancellation"
          label="Echo cancellation"
          description="Reduces loopback and speaker bleed for headset and speakerphone use."
          icon={<Mic className="h-4 w-4 contrast-icon-muted" aria-hidden="true" />}
          controlClassName={toggleControlClass}
        >
          <Switch
            checked={settings.echoCancellation}
            onCheckedChange={(checked) =>
              updateSettings({ echoCancellation: checked })
            }
          />
        </SettingsRow>

        <SettingsRow
          id="recording-noise-suppression"
          label="Noise suppression"
          description="Reduces steady background noise before the mic is mixed into the recording."
          icon={<Mic className="h-4 w-4 contrast-icon-muted" aria-hidden="true" />}
          controlClassName={toggleControlClass}
        >
          <Switch
            checked={settings.noiseSuppression}
            onCheckedChange={(checked) =>
              updateSettings({ noiseSuppression: checked })
            }
          />
        </SettingsRow>

        <SettingsRow
          id="recording-browser-auto-gain"
          label="Browser auto gain"
          description="Lets the browser lift a quiet microphone before Nojoin applies its own balancing."
          icon={<Mic className="h-4 w-4 contrast-icon-muted" aria-hidden="true" />}
          controlClassName={toggleControlClass}
        >
          <Switch
            checked={settings.autoGainControl}
            onCheckedChange={(checked) =>
              updateSettings({ autoGainControl: checked })
            }
          />
        </SettingsRow>
      </SettingsCard>

      <SettingsCard
        id="recording-quiet-reminders"
        title="Quiet-Audio Reminders"
        description="Browser-local prompts warning that a recording captured little or no sound."
      >
        <SettingsRow
          label="Reminders"
          description={
            suppressQuietAudioWarnings
              ? "Currently suppressed. Reset them to see the prompts again."
              : "Currently enabled."
          }
          controlClassName="sm:min-w-0 sm:flex sm:justify-end"
        >
          <button
            type="button"
            onClick={() => {
              resetWarnings();
              addNotification({
                type: "success",
                message: "Audio warnings have been reset.",
              });
            }}
            className={SETTINGS_BUTTON_SECONDARY}
          >
            Reset warnings
          </button>
        </SettingsRow>
      </SettingsCard>
    </>
  );
}
