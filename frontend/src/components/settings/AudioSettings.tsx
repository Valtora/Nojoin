"use client";

import { CompanionDevices } from "@/types";
import { useAudioWarningStore } from "@/lib/audioWarningStore";
import { fuzzyMatch } from "@/lib/searchUtils";
import { AUDIO_KEYWORDS } from "./keywords";
import { sanitizeIntegerString } from "@/lib/validation";
import { useState } from "react";
import { useServiceStatusStore } from "@/lib/serviceStatusStore";
import { useNotificationStore } from "@/lib/notificationStore";
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
  const { checkCompanion, enableCompanionMonitoring } = useServiceStatusStore();
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
    <div className="space-y-8">
      {showDevices && (
        <div>
          <div className="mb-4 flex items-center justify-between gap-3">
            <h3 className="text-lg font-medium text-gray-900 dark:text-white">
              Recording Devices
            </h3>
            <button
              onClick={handleTestConnection}
              disabled={testingConnection}
              className="inline-flex items-center gap-2 rounded-lg border border-gray-300 bg-white px-3 py-2 text-xs font-medium text-gray-700 transition-colors hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-60 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-200 dark:hover:bg-gray-800"
            >
              {testingConnection ? (
                <Loader2 className="w-3 h-3 animate-spin" />
              ) : (
                <RefreshCw className="w-3 h-3" />
              )}
              Refresh devices
            </button>
          </div>
          <div className="max-w-xl space-y-4">
            {companionDevices ? (
              <>
                {connectionResult === "success" && (
                  <div className="rounded-lg border border-green-200 bg-green-50 px-3 py-2 text-xs font-medium text-green-700 dark:border-green-500/20 dark:bg-green-500/10 dark:text-green-300">
                    Companion device list refreshed.
                  </div>
                )}

                <div>
                  <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                    <div className="flex items-center gap-2">
                      <Mic className="w-4 h-4" />
                      Input Device (Microphone)
                    </div>
                  </label>
                  <select
                    value={selectedInputDevice || ""}
                    onChange={(e) =>
                      onSelectInputDevice(e.target.value || null)
                    }
                    className="w-full p-2 rounded-lg border border-gray-400 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:ring-2 focus:ring-orange-500 focus:border-transparent"
                  >
                    <option value="">System Default</option>
                    {companionDevices.input_devices.map((device) => (
                      <option key={device.name} value={device.name}>
                        {device.name}
                        {device.is_default ? " (Default)" : ""}
                      </option>
                    ))}
                  </select>
                  <p className="text-xs text-gray-500 mt-1">
                    The microphone to capture your voice.
                  </p>
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                    <div className="flex items-center gap-2">
                      <Speaker className="w-4 h-4" />
                      Output Device (System Audio)
                    </div>
                  </label>
                  <select
                    value={selectedOutputDevice || ""}
                    onChange={(e) =>
                      onSelectOutputDevice(e.target.value || null)
                    }
                    className="w-full p-2 rounded-lg border border-gray-400 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:ring-2 focus:ring-orange-500 focus:border-transparent"
                  >
                    <option value="">System Default</option>
                    {companionDevices.output_devices.map((device) => (
                      <option key={device.name} value={device.name}>
                        {device.name}
                        {device.is_default ? " (Default)" : ""}
                      </option>
                    ))}
                  </select>
                  <p className="text-xs text-gray-500 mt-1">
                    The audio output to capture system sounds (loopback).
                  </p>
                </div>

                <div className="pt-4 border-t border-gray-200 dark:border-gray-700">
                  <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                    Minimum Meeting Length (Minutes)
                  </label>
                  <input
                    type="text"
                    inputMode="numeric"
                    value={
                      companionConfig?.min_meeting_length?.toString() || "0"
                    }
                    onChange={handleMinLengthChange}
                    className={`w-full p-2 rounded-lg border ${localError ? "border-red-500 focus:ring-red-500" : "border-gray-400 dark:border-gray-600 focus:ring-orange-500"} bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:ring-2 focus:border-transparent`}
                  />
                  {localError && (
                    <p className="text-xs text-red-500 mt-1 flex items-center gap-1">
                      <AlertCircle className="w-3 h-3" />
                      {localError}
                    </p>
                  )}
                  <p className="text-xs text-gray-500 mt-1">
                    Recordings shorter than this will be automatically
                    discarded. Set to 0 to disable.
                  </p>
                </div>
              </>
            ) : (
              <div className="p-4 bg-yellow-100 dark:bg-yellow-900/20 border border-yellow-300 dark:border-yellow-800 rounded-lg">
                <p className="text-sm font-medium text-yellow-800 dark:text-yellow-200 mb-2">
                  Device settings unavailable
                </p>
                <p className="text-xs text-yellow-700 dark:text-yellow-300 mb-3">
                  Nojoin could not load the current Companion device list. Use the Companion App connection section above to pair or reconnect, then retry here.
                </p>

                <div className="flex items-center gap-3">
                  <button
                    onClick={handleTestConnection}
                    disabled={testingConnection}
                    className="flex items-center px-3 py-1.5 text-xs font-medium text-white bg-yellow-600 rounded-md hover:bg-yellow-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
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
        </div>
      )}

      {showWarnings && (
        <div>
          <h3 className="text-lg font-medium text-gray-900 dark:text-white mb-4">
            Audio Warnings
          </h3>
          <div className="max-w-xl rounded-xl border border-gray-200 bg-white p-4 shadow-sm dark:border-gray-700 dark:bg-gray-900">
            <p className="text-sm font-medium text-gray-900 dark:text-white">
              Quiet-audio reminders
            </p>
            <p className="mt-1 text-sm text-gray-600 dark:text-gray-300">
              Recording-time quiet-audio reminders can be dismissed for the rest of the current meeting or turned off permanently for advanced workflows.
            </p>
            <div className="mt-3 rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 text-xs text-gray-600 dark:border-gray-700 dark:bg-gray-950 dark:text-gray-300">
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
                className="inline-flex items-center rounded-lg border border-orange-300 bg-orange-50 px-4 py-2 text-sm font-medium text-orange-800 transition-colors hover:bg-orange-100 dark:border-orange-500/30 dark:bg-orange-500/10 dark:text-orange-100 dark:hover:bg-orange-500/20"
              >
                Reset warnings
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
