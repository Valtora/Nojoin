"use client";

import { CompanionDevices } from "@/types";
import { useAudioWarningStore } from "@/lib/audioWarningStore";
import { fuzzyMatch } from "@/lib/searchUtils";
import { AUDIO_KEYWORDS } from "./keywords";
import { sanitizeIntegerString } from "@/lib/validation";
import { useState } from "react";
import { useServiceStatusStore } from "@/lib/serviceStatusStore";
import { useNotificationStore } from "@/lib/notificationStore";
import { COMPANION_LOCAL_CONNECTION_UNAVAILABLE_MESSAGE } from "@/lib/companionLocalApi";
import {
  AlertCircle,
  Loader2,
  Mic,
  RefreshCw,
  Speaker,
  XCircle,
} from "lucide-react";
import SettingsCallout from "./SettingsCallout";
import SettingsField from "./SettingsField";
import SettingsPanel from "./SettingsPanel";

interface AudioSettingsProps {
  companionConfig: {
    api_port: number;
    local_port: number;
    min_meeting_length?: number;
  } | null;
  onUpdateCompanionConfig: (config: {
    api_port?: number;
    min_meeting_length?: number;
  }) => void;
  onRefreshCompanionConfig?: () => Promise<boolean>;
  companionDevices: CompanionDevices | null;
  selectedInputDevice: string | null;
  onSelectInputDevice: (device: string | null) => void;
  selectedOutputDevice: string | null;
  onSelectOutputDevice: (device: string | null) => void;
  searchQuery?: string;
  suppressNoMatch?: boolean;
}

const CONTROL_STYLES =
  "w-full rounded-xl border border-gray-300 bg-white px-3 py-2.5 text-sm text-gray-900 focus:border-transparent focus:ring-2 focus:ring-orange-500 dark:border-gray-700 dark:bg-gray-900 dark:text-white";

const SECONDARY_BUTTON_STYLES =
  "inline-flex items-center gap-2 rounded-xl border border-gray-300 bg-white px-3 py-2 text-xs font-semibold text-gray-700 transition hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-60 dark:border-gray-700 dark:bg-gray-950 dark:text-gray-200 dark:hover:bg-gray-900";

export default function AudioSettings({
  companionConfig,
  onUpdateCompanionConfig,
  onRefreshCompanionConfig,
  companionDevices,
  selectedInputDevice,
  onSelectInputDevice,
  selectedOutputDevice,
  onSelectOutputDevice,
  searchQuery = "",
  suppressNoMatch = false,
}: AudioSettingsProps) {
  const showDevices = fuzzyMatch(searchQuery, AUDIO_KEYWORDS);
  const showWarnings = fuzzyMatch(searchQuery, [
    "warning",
    "warnings",
    "dismiss",
    "quiet",
    "silence",
    "reset warnings",
  ]);
  const [localError, setLocalError] = useState<string | null>(null);

  const [testingConnection, setTestingConnection] = useState(false);
  const [connectionResult, setConnectionResult] = useState<
    "success" | "error" | null
  >(null);
  const {
    checkCompanion,
    companionLocalConnectionUnavailable,
    enableCompanionMonitoring,
  } = useServiceStatusStore();
  const suppressQuietAudioWarnings = useAudioWarningStore(
    (state) => state.suppressQuietAudioWarnings,
  );
  const resetWarnings = useAudioWarningStore((state) => state.resetWarnings);
  const { addNotification } = useNotificationStore();

  const handleTestConnection = async () => {
    setTestingConnection(true);
    setConnectionResult(null);
    try {
      const refreshed = onRefreshCompanionConfig
        ? await onRefreshCompanionConfig()
        : false;

      if (refreshed) {
        enableCompanionMonitoring();
        await checkCompanion();
        setConnectionResult("success");
      } else {
        setConnectionResult("error");
      }
    } catch {
      setConnectionResult("error");
    } finally {
      setTestingConnection(false);
      // Clear result after 3 seconds
      setTimeout(() => setConnectionResult(null), 3000);
    }
  };

  const handleMinLengthChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const val = e.target.value;
    const sanitized = sanitizeIntegerString(val);
    const num = parseInt(sanitized, 10);

    if (isNaN(num)) {
      onUpdateCompanionConfig({ min_meeting_length: 0 });
      setLocalError(null);
      return;
    }

    if (num > 1440) {
      setLocalError("Maximum length is 1440 minutes (24 hours).");
    } else {
      setLocalError(null);
    }

    // Updates value. Input allows free typing; validation handled by parent.
    onUpdateCompanionConfig({ min_meeting_length: num });
  };

  if (!showDevices && !showWarnings && searchQuery) {
    return suppressNoMatch ? null : (
      <SettingsCallout
        tone="neutral"
        title="No matching settings"
        message="Try a broader search term for recording devices, quiet audio, or meeting length."
      />
    );
  }

  return (
    <div className="space-y-4">
      {showDevices && (
        <SettingsPanel as="section" variant="subtle" className="space-y-4">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
            <div>
              <div className="text-[11px] font-semibold uppercase tracking-[0.18em] text-gray-500 dark:text-gray-400">
                Recording devices
              </div>
              <h4 className="mt-2 text-base font-semibold text-gray-900 dark:text-white">
                Input, output, and capture thresholds
              </h4>
              <p className="mt-2 text-sm leading-6 text-gray-600 dark:text-gray-300">
                Choose which microphone and system output the local Companion records, then set the minimum meeting length to keep. Pairing, installer, and browser recovery actions stay in the section above.
              </p>
            </div>
            <button
              onClick={handleTestConnection}
              disabled={testingConnection}
              className={SECONDARY_BUTTON_STYLES}
            >
              {testingConnection ? (
                <Loader2 className="w-3 h-3 animate-spin" />
              ) : (
                <RefreshCw className="w-3 h-3" />
              )}
              Refresh devices
            </button>
          </div>

          <div className="space-y-4">
            {companionDevices ? (
              <>
                {connectionResult === "success" && (
                  <SettingsCallout
                    tone="success"
                    message="Companion device list refreshed."
                  />
                )}

                <div className="grid gap-4 lg:grid-cols-2">
                  <SettingsField
                    label="Input device"
                    icon={<Mic className="h-4 w-4" />}
                    description="Microphone used to capture your voice."
                  >
                    <select
                      value={selectedInputDevice || ""}
                      onChange={(e) => onSelectInputDevice(e.target.value || null)}
                      className={CONTROL_STYLES}
                    >
                      <option value="">System Default</option>
                      {companionDevices.input_devices.map((device) => (
                        <option key={device.name} value={device.name}>
                          {device.name}
                          {device.is_default ? " (Default)" : ""}
                        </option>
                      ))}
                    </select>
                  </SettingsField>

                  <SettingsField
                    label="Output device"
                    icon={<Speaker className="h-4 w-4" />}
                    description="System output captured for loopback audio."
                  >
                    <select
                      value={selectedOutputDevice || ""}
                      onChange={(e) => onSelectOutputDevice(e.target.value || null)}
                      className={CONTROL_STYLES}
                    >
                      <option value="">System Default</option>
                      {companionDevices.output_devices.map((device) => (
                        <option key={device.name} value={device.name}>
                          {device.name}
                          {device.is_default ? " (Default)" : ""}
                        </option>
                      ))}
                    </select>
                  </SettingsField>

                  <SettingsField
                    label="Minimum meeting length (minutes)"
                    description="Recordings shorter than this are discarded automatically. Use 0 to disable the cutoff, or raise it to filter out tests and accidental starts."
                    className="lg:col-span-2"
                  >
                    <input
                      type="text"
                      inputMode="numeric"
                      value={companionConfig?.min_meeting_length?.toString() || "0"}
                      onChange={handleMinLengthChange}
                      className={`${CONTROL_STYLES} ${localError ? "border-red-500 focus:ring-red-500 dark:border-red-500/70" : ""}`}
                    />
                    {localError && (
                      <p className="mt-2 flex items-center gap-1 text-xs text-red-500 dark:text-red-300">
                        <AlertCircle className="w-3 h-3" />
                        {localError}
                      </p>
                    )}
                  </SettingsField>
                </div>
              </>
            ) : (
              <SettingsCallout tone="warning" title="Device settings unavailable">
                <div>
                  <p className="leading-6">
                    {companionLocalConnectionUnavailable
                      ? COMPANION_LOCAL_CONNECTION_UNAVAILABLE_MESSAGE
                      : "Nojoin could not load the current Companion device list. Use the connection and pairing section above to pair or reconnect, then retry here."}
                  </p>

                  <div className="mt-3 flex items-center gap-3">
                    <button
                      onClick={handleTestConnection}
                      disabled={testingConnection}
                      className="inline-flex items-center rounded-xl bg-amber-600 px-3 py-2 text-xs font-semibold text-white transition hover:bg-amber-700 disabled:cursor-not-allowed disabled:opacity-60"
                    >
                      {testingConnection ? (
                        <>
                          <Loader2 className="mr-2 h-3 w-3 animate-spin" />
                          Connecting...
                        </>
                      ) : (
                        <>
                          <RefreshCw className="mr-2 h-3 w-3" />
                          Retry Connection
                        </>
                      )}
                    </button>
                    {connectionResult === "error" && (
                      <span className="flex animate-pulse items-center text-xs text-red-600 dark:text-red-400">
                        <XCircle className="mr-1 h-3 w-3" />
                        Failed
                      </span>
                    )}
                  </div>
                </div>
              </SettingsCallout>
            )}
          </div>
        </SettingsPanel>
      )}

      {showWarnings && (
        <SettingsPanel as="section" variant="subtle" className="space-y-4">
          <div>
            <div className="text-[11px] font-semibold uppercase tracking-[0.18em] text-gray-500 dark:text-gray-400">
              Audio warnings
            </div>
            <h4 className="mt-2 text-base font-semibold text-gray-900 dark:text-white">
              Quiet-audio reminders
            </h4>
            <p className="mt-2 text-sm leading-6 text-gray-600 dark:text-gray-300">
              Recording-time quiet-audio reminders are local workflow aids. Reset them here if you want warning prompts to appear again after you previously dismissed them.
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
      )}
    </div>
  );
}
