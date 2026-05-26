"use client";

import { useEffect, useState } from "react";
import { Loader2, Mic, RefreshCw, Volume2 } from "lucide-react";

import { useAudioWarningStore } from "@/lib/audioWarningStore";
import { useCapture } from "@/lib/capture/CaptureProvider";
import { fuzzyMatch } from "@/lib/searchUtils";
import { useNotificationStore } from "@/lib/notificationStore";

import { AUDIO_KEYWORDS } from "./keywords";
import SettingsCallout from "./SettingsCallout";
import SettingsField from "./SettingsField";
import SettingsPanel from "./SettingsPanel";

interface CaptureSettingsProps {
  searchQuery?: string;
  suppressNoMatch?: boolean;
}

interface MicrophoneOption {
  deviceId: string;
  label: string;
}

const CONTROL_STYLES =
  "w-full rounded-xl border border-gray-300 bg-white px-3 py-2.5 text-sm text-gray-900 focus:border-transparent focus:ring-2 focus:ring-orange-500 dark:border-gray-700 dark:bg-gray-900 dark:text-white";

const SECONDARY_BUTTON_STYLES =
  "inline-flex items-center gap-2 rounded-xl border border-gray-300 bg-white px-3 py-2 text-xs font-semibold text-gray-700 transition hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-60 dark:border-gray-700 dark:bg-gray-950 dark:text-gray-200 dark:hover:bg-gray-900";

export default function CaptureSettings({
  searchQuery = "",
  suppressNoMatch = false,
}: CaptureSettingsProps) {
  const showDevices = fuzzyMatch(searchQuery, AUDIO_KEYWORDS);
  const showWarnings = fuzzyMatch(searchQuery, [
    "warning",
    "warnings",
    "dismiss",
    "quiet",
    "silence",
    "reset warnings",
  ]);
  const { addNotification } = useNotificationStore();
  const { settings, updateSettings } = useCapture();
  const suppressQuietAudioWarnings = useAudioWarningStore(
    (state) => state.suppressQuietAudioWarnings,
  );
  const resetWarnings = useAudioWarningStore((state) => state.resetWarnings);
  const [microphones, setMicrophones] = useState<MicrophoneOption[]>([]);
  const [loadingDevices, setLoadingDevices] = useState(false);
  const [deviceError, setDeviceError] = useState<string | null>(null);

  useEffect(() => {
    if (!navigator.mediaDevices?.enumerateDevices) {
      setDeviceError(
        "This browser cannot enumerate microphone devices yet. Grant microphone permission, then refresh the list.",
      );
      return;
    }

    let disposed = false;

    const loadDevices = async () => {
      setLoadingDevices(true);
      try {
        const devices = await navigator.mediaDevices.enumerateDevices();
        if (disposed) {
          return;
        }

        const nextMicrophones = devices
          .filter((device) => device.kind === "audioinput")
          .map((device, index) => ({
            deviceId: device.deviceId,
            label: device.label || `Microphone ${index + 1}`,
          }));

        setMicrophones(nextMicrophones);
        setDeviceError(null);
      } catch (error) {
        if (disposed) {
          return;
        }

        setDeviceError(
          error instanceof Error
            ? error.message
            : "Failed to refresh microphone devices.",
        );
      } finally {
        if (!disposed) {
          setLoadingDevices(false);
        }
      }
    };

    void loadDevices();

    const handleDeviceChange = () => {
      void loadDevices();
    };

    navigator.mediaDevices.addEventListener?.("devicechange", handleDeviceChange);

    return () => {
      disposed = true;
      navigator.mediaDevices.removeEventListener?.("devicechange", handleDeviceChange);
    };
  }, []);

  const refreshDevices = async () => {
    if (!navigator.mediaDevices?.enumerateDevices) {
      setDeviceError(
        "This browser cannot enumerate microphone devices yet. Grant microphone permission, then refresh the list.",
      );
      return;
    }

    setLoadingDevices(true);
    try {
      const devices = await navigator.mediaDevices.enumerateDevices();
      const nextMicrophones = devices
        .filter((device) => device.kind === "audioinput")
        .map((device, index) => ({
          deviceId: device.deviceId,
          label: device.label || `Microphone ${index + 1}`,
        }));
      setMicrophones(nextMicrophones);
      setDeviceError(null);
    } catch (error) {
      setDeviceError(
        error instanceof Error
          ? error.message
          : "Failed to refresh microphone devices.",
      );
    } finally {
      setLoadingDevices(false);
    }
  };

  if (!showDevices && !showWarnings && searchQuery) {
    return suppressNoMatch ? null : (
      <SettingsCallout
        tone="neutral"
        title="No matching settings"
        message="Try a broader search term for microphone selection, automatic levels, or quiet audio warnings."
      />
    );
  }

  return (
    <div className="space-y-4">
      {showDevices ? (
        <SettingsPanel as="section" variant="subtle" className="space-y-4">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
            <div>
              <div className="text-[11px] font-semibold uppercase tracking-[0.18em] text-gray-500 dark:text-gray-400">
                Browser capture
              </div>
              <h4 className="mt-2 text-base font-semibold text-gray-900 dark:text-white">
                Microphone and automatic levels
              </h4>
              <p className="mt-2 text-sm leading-6 text-gray-600 dark:text-gray-300">
                Select the microphone added to shared audio. Nojoin balances system and microphone levels during recording.
              </p>
            </div>
            <button
              type="button"
              onClick={() => void refreshDevices()}
              disabled={loadingDevices}
              className={SECONDARY_BUTTON_STYLES}
            >
              {loadingDevices ? (
                <Loader2 className="h-3 w-3 animate-spin" />
              ) : (
                <RefreshCw className="h-3 w-3" />
              )}
              Refresh devices
            </button>
          </div>

          {deviceError ? (
            <SettingsCallout tone="warning" title="Microphone list unavailable">
              <p className="leading-6">{deviceError}</p>
            </SettingsCallout>
          ) : null}

          <div className="grid gap-4 lg:grid-cols-2">
            <SettingsField
              label="Microphone"
              icon={<Mic className="h-4 w-4" />}
              description="This input is mixed with shared tab or system audio during capture."
            >
              <select
                value={settings.microphoneDeviceId || ""}
                onChange={(event) =>
                  updateSettings({
                    microphoneDeviceId: event.target.value || null,
                  })
                }
                className={CONTROL_STYLES}
              >
                <option value="">System default</option>
                {microphones.map((device) => (
                  <option key={device.deviceId} value={device.deviceId}>
                    {device.label}
                  </option>
                ))}
              </select>
            </SettingsField>

            <SettingsField
              label="Automatic levels"
              icon={<Volume2 className="h-4 w-4" />}
              description="Capture levels are balanced continuously while recording."
            >
              <div className="rounded-xl border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs font-semibold text-emerald-800 dark:border-emerald-500/20 dark:bg-emerald-500/10 dark:text-emerald-100">
                Enabled
              </div>
            </SettingsField>
          </div>
        </SettingsPanel>
      ) : null}

      {showWarnings ? (
        <SettingsPanel as="section" variant="subtle" className="space-y-4">
          <div>
            <div className="text-[11px] font-semibold uppercase tracking-[0.18em] text-gray-500 dark:text-gray-400">
              Audio warnings
            </div>
            <h4 className="mt-2 text-base font-semibold text-gray-900 dark:text-white">
              Quiet-audio reminders
            </h4>
            <p className="mt-2 text-sm leading-6 text-gray-600 dark:text-gray-300">
              Quiet-audio reminders are browser-local workflow aids. Reset them here if you want warning prompts to appear again after you dismissed them.
            </p>
          </div>

          <SettingsPanel variant="field">
            <div className="rounded-xl border border-gray-200/80 bg-gray-50 px-3 py-2 text-xs text-gray-600 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-300">
              Current status: {suppressQuietAudioWarnings ? "suppressed" : "enabled"}
            </div>

            <div className="mt-4 flex flex-wrap gap-3">
              <button
                type="button"
                onClick={() => {
                  resetWarnings();
                  addNotification({
                    type: "success",
                    message: "Audio warnings have been reset.",
                  });
                }}
                className="inline-flex items-center rounded-xl border border-orange-300 bg-orange-50 px-4 py-2.5 text-sm font-semibold text-orange-800 transition-colors hover:bg-orange-100 dark:border-orange-500/30 dark:bg-orange-500/10 dark:text-orange-100 dark:hover:bg-orange-500/20"
              >
                Reset warnings
              </button>
            </div>
          </SettingsPanel>
        </SettingsPanel>
      ) : null}
    </div>
  );
}