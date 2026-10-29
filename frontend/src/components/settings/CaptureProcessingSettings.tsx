"use client";

import { ButtonBase } from "@/components/ui/nativeBases";
import { useAudioWarningStore } from "@/lib/audioWarningStore";
import { useCapture } from "@/lib/capture/CaptureProvider";
import { useNotificationStore } from "@/lib/notificationStore";
import { Switch } from "@/components/ui/Switch";

import SettingsCard from "./SettingsCard";
import { useSettingsContext } from "./SettingsProvider";
import SettingsRow from "./SettingsRow";
import { SETTINGS_BUTTON_SECONDARY, SETTINGS_CONTROL_END } from "./settingsControls";

/**
 * The browser-side audio processing toggles, the quiet-audio reminder reset and
 * the recording consent notice, which can be turned off from the notice itself.
 *
 * Separate from CaptureSettings because these sit behind the Advanced gate while
 * device selection and levels do not: the defaults suit most recordings, and
 * these controls are touched only when something sounds wrong.
 */
export default function CaptureProcessingSettings() {
  const { addNotification } = useNotificationStore();
  const { settings, updateSettings } = useCapture();
  const suppressQuietAudioWarnings = useAudioWarningStore(
    (state) => state.suppressQuietAudioWarnings,
  );
  const resetWarnings = useAudioWarningStore((state) => state.resetWarnings);
  const { settings: accountSettings, persistNow } = useSettingsContext();

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
          controlClassName={SETTINGS_CONTROL_END}
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
          description="Reduces steady background noise before the mic is mixed into the recording. Off by default, because it makes speaker labels less accurate in most rooms."
          controlClassName={SETTINGS_CONTROL_END}
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
          controlClassName={SETTINGS_CONTROL_END}
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
          controlClassName={SETTINGS_CONTROL_END}
        >
          {/* They are only ever turned off from the warning itself, so the
              row offers the way back and nothing while they are on. */}
          {suppressQuietAudioWarnings && (
            <ButtonBase
              data-ui-fill="card"
              data-ui-hover="inset"
              data-ui-active="inset"
              data-ui-border="full"
              chrome="action"
              type="button"
              onClick={() => {
                resetWarnings();
                addNotification({
                  type: "success",
                  message: "Quiet audio warnings are back on.",
                });
              }}
              className={SETTINGS_BUTTON_SECONDARY}
            >
              Turn back on
            </ButtonBase>
          )}
        </SettingsRow>
      </SettingsCard>

      <SettingsCard
        id="recording-consent-notice"
        title="Recording consent notice"
        description="A reminder, shown when you start a recording, with a suggested statement asking the meeting's agreement."
      >
        <SettingsRow
          labelId="recording-consent-notice-label"
          descriptionId="recording-consent-notice-help"
          label="Show the notice"
          description="Applies to recordings you start on this account. Closing the notice never stops a recording."
          controlClassName={SETTINGS_CONTROL_END}
        >
          <Switch
            aria-labelledby="recording-consent-notice-label"
            aria-describedby="recording-consent-notice-help"
            checked={accountSettings.hide_recording_consent_notice !== true}
            onCheckedChange={(checked) =>
              void persistNow({ ...accountSettings, hide_recording_consent_notice: !checked })
            }
          />
        </SettingsRow>
      </SettingsCard>
    </>
  );
}
