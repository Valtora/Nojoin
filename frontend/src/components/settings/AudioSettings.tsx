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

const PANEL_STYLES =
  "rounded-2xl border border-gray-200/80 bg-gray-50/85 p-5 dark:border-gray-800 dark:bg-gray-900/70";

const FIELD_CARD_STYLES =
  "rounded-2xl border border-gray-200/80 bg-white/90 p-4 dark:border-gray-800 dark:bg-gray-950/80";

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
    return suppressNoMatch ? null : <div className="text-gray-500">No matching settings found.</div>;
  }

  return (
    <div className="space-y-4">
      {showDevices && (
        <section className={`${PANEL_STYLES} space-y-4`}>
          <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
            <div>
              <div className="text-[11px] font-semibold uppercase tracking-[0.18em] text-gray-500 dark:text-gray-400">
                Recording devices
              </div>
              <h4 className="mt-2 text-base font-semibold text-gray-900 dark:text-white">
                Input, output, and capture thresholds
              </h4>
              <p className="mt-2 text-sm leading-6 text-gray-600 dark:text-gray-300">
                Choose which microphone and system output the Companion records, then set the minimum meeting length to keep.
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
                  <div className="rounded-xl border border-green-200/80 bg-green-50/80 px-4 py-3 text-sm text-green-700 dark:border-green-500/20 dark:bg-green-500/10 dark:text-green-300">
                    Companion device list refreshed.
                  </div>
                )}

                <div className="grid gap-4 lg:grid-cols-2">
                  <div className={FIELD_CARD_STYLES}>
                    <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                      <span className="flex items-center gap-2">
                        <Mic className="w-4 h-4" />
                        Input device
                      </span>
                    </label>
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
                    <p className="mt-2 text-xs text-gray-500 dark:text-gray-400">
                      Microphone used to capture your voice.
                    </p>
                  </div>

                  <div className={FIELD_CARD_STYLES}>
                    <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                      <span className="flex items-center gap-2">
                        <Speaker className="w-4 h-4" />
                        Output device
                      </span>
                    </label>
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
                    <p className="mt-2 text-xs text-gray-500 dark:text-gray-400">
                      System output captured for loopback audio.
                    </p>
                  </div>

                  <div className={`${FIELD_CARD_STYLES} lg:col-span-2`}>
                    <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                      Minimum meeting length (minutes)
                    </label>
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
                    <p className="mt-2 text-xs text-gray-500 dark:text-gray-400">
                      Recordings shorter than this are discarded automatically. Use 0 to disable the cutoff.
                    </p>
                  </div>
                </div>
              </>
            ) : (
              <div className="rounded-2xl border border-amber-200/80 bg-amber-50/80 p-4 dark:border-amber-500/20 dark:bg-amber-500/10">
                <p className="text-sm font-medium text-amber-800 dark:text-amber-200 mb-2">
                  Device settings unavailable
                </p>
                <p className="text-sm leading-6 text-amber-700 dark:text-amber-300 mb-3">
                  {companionLocalConnectionUnavailable
                    ? COMPANION_LOCAL_CONNECTION_UNAVAILABLE_MESSAGE
                    : "Nojoin could not load the current Companion device list. Use the Companion App connection section above to pair or reconnect, then retry here."}
                </p>

                <div className="flex items-center gap-3">
                  <button
                    onClick={handleTestConnection}
                    disabled={testingConnection}
                    className="inline-flex items-center rounded-xl bg-amber-600 px-3 py-2 text-xs font-semibold text-white transition hover:bg-amber-700 disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    {testingConnection ? (
                      <>
                        <Loader2 className="w-3 h-3 mr-2 animate-spin" />
                        Connecting...
                      </>
                    ) : (
                      <>
                        <RefreshCw className="w-3 h-3 mr-2" />
                        Retry Connection
                      </>
                    )}
                  </button>
                  {connectionResult === "error" && (
                    <span className="flex items-center text-xs text-red-600 dark:text-red-400 animate-pulse">
                      <XCircle className="w-3 h-3 mr-1" />
                      Failed
                    </span>
                  )}
                </div>
              </div>
            )}
          </div>
        </section>
      )}

      {showWarnings && (
        <section className={`${PANEL_STYLES} space-y-4`}>
          <div>
            <div className="text-[11px] font-semibold uppercase tracking-[0.18em] text-gray-500 dark:text-gray-400">
              Audio warnings
            </div>
            <h4 className="mt-2 text-base font-semibold text-gray-900 dark:text-white">
              Quiet-audio reminders
            </h4>
            <p className="mt-2 text-sm leading-6 text-gray-600 dark:text-gray-300">
              Recording-time quiet-audio reminders can be dismissed for the rest of the current meeting or turned off permanently for advanced workflows.
            </p>
          </div>

          <div className={FIELD_CARD_STYLES}>
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
          </div>
        </section>
      )}
    </div>
  );
}
