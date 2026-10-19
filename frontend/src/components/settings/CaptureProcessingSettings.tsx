"use client";

import { Mic } from "lucide-react";

import { useAudioWarningStore } from "@/lib/audioWarningStore";
import { useCapture } from "@/lib/capture/CaptureProvider";
import { useNotificationStore } from "@/lib/notificationStore";
import { Switch } from "@/components/ui/Switch";

import SettingsCard from "./SettingsCard";
import SettingsRow from "./SettingsRow";
import { SETTINGS_BUTTON_SECONDARY } from "./settingsControls";

/**
 * The browser-side audio processing toggles and the quiet-audio reminder reset.
 *
 * Separate from CaptureSettings because these sit behind the Advanced gate while
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

  const toggleControlClass = "@min-[26rem]:min-w-0 @min-[26rem]:flex @min-[26rem]:justify-end";

  return (
    <>
      <SettingsCard
        id="recording-browser-processing"
        title="Browser processing"
        description="What the browser does to the microphone before Nojoin receives it. Changes apply at the next input test, recording start or resume."
      >

        <SettingsRow
          id="recording-echo-cancellation"
          labelId="recording-echo-cancellation-label"
          descriptionId="recording-echo-cancellation-help"
          label="Echo cancellation"
          description="Reduces loopback and speaker bleed for headset and speakerphone use."
          icon={<Mic className="h-4 w-4 contrast-icon-muted" aria-hidden="true" />}
          controlClassName={toggleControlClass}
        >
          <Switch
            aria-labelledby="recording-echo-cancellation-label"
            aria-describedby="recording-echo-cancellation-help"
            checked={settings.echoCancellation}
            onCheckedChange={(checked) =>
              updateSettings({ echoCancellation: checked })
            }
          />
        </SettingsRow>

        <SettingsRow
          id="recording-noise-suppression"
          labelId="recording-noise-suppression-label"
          descriptionId="recording-noise-suppression-help"
          label="Noise suppression"
          description="Reduces steady background noise before the mic is mixed into the recording."
          icon={<Mic className="h-4 w-4 contrast-icon-muted" aria-hidden="true" />}
          controlClassName={toggleControlClass}
        >
          <Switch
            aria-labelledby="recording-noise-suppression-label"
            aria-describedby="recording-noise-suppression-help"
            checked={settings.noiseSuppression}
            onCheckedChange={(checked) =>
              updateSettings({ noiseSuppression: checked })
            }
          />
        </SettingsRow>

        <SettingsRow
          id="recording-browser-auto-gain"
          labelId="recording-browser-auto-gain-label"
          descriptionId="recording-browser-auto-gain-help"
          label="Browser auto gain"
          description="Lets the browser lift a quiet microphone before Nojoin applies its own balancing."
          icon={<Mic className="h-4 w-4 contrast-icon-muted" aria-hidden="true" />}
          controlClassName={toggleControlClass}
        >
          <Switch
            aria-labelledby="recording-browser-auto-gain-label"
            aria-describedby="recording-browser-auto-gain-help"
            checked={settings.autoGainControl}
            onCheckedChange={(checked) =>
              updateSettings({ autoGainControl: checked })
            }
          />
        </SettingsRow>
      </SettingsCard>

      <SettingsCard
        id="recording-quiet-reminders"
        title="Quiet audio warnings"
        description="This browser warns you when a recording picks up little or no sound."
      >
        <SettingsRow
          label="Warnings"
          description={
            suppressQuietAudioWarnings
              ? "Turned off in this browser."
              : "On in this browser."
          }
          controlClassName="@min-[26rem]:min-w-0 @min-[26rem]:flex @min-[26rem]:justify-end"
        >
          <button
            type="button"
            onClick={() => {
              resetWarnings();
              addNotification({
                type: "success",
                message: "Quiet audio warnings are back on.",
              });
            }}
            disabled={!suppressQuietAudioWarnings}
            className={SETTINGS_BUTTON_SECONDARY}
          >
            Turn back on
          </button>
        </SettingsRow>
      </SettingsCard>
    </>
  );
}
