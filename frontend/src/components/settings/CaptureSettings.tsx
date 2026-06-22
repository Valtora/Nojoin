"use client";

import { useEffect, useRef, useState } from "react";
import { Loader2, Mic, RefreshCw, Volume2 } from "lucide-react";

import { useAudioWarningStore } from "@/lib/audioWarningStore";
import { useCapture } from "@/lib/capture/CaptureProvider";
import { fuzzyMatch } from "@/lib/searchUtils";
import { useNotificationStore } from "@/lib/notificationStore";
import { getErrorMessage } from "@/lib/errors";

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

const GAIN_MIN = 0;
const GAIN_MAX = 3;
const GAIN_STEP = 0.05;

const clampPreviewLevel = (value: number) =>
  Math.max(0, Math.min(100, Math.round(value)));

const formatGainLabel = (value: number) => `${value.toFixed(2)}x`;

const MeterBar = ({
  label,
  value,
  tone,
}: {
  label: string;
  value: number;
  tone: "orange" | "emerald";
}) => {
  const backgroundClass =
    tone === "orange"
      ? "bg-gradient-to-r from-orange-500 via-orange-400 to-amber-300"
      : "bg-gradient-to-r from-emerald-500 via-emerald-400 to-teal-300";

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between text-xs font-semibold uppercase tracking-[0.18em] text-gray-500 dark:text-gray-400">
        <span>{label}</span>
        <span>{value}%</span>
      </div>
      <div className="h-3 overflow-hidden rounded-full bg-gray-200 dark:bg-gray-800">
        <div
          className={`h-full rounded-full transition-[width] duration-75 ${backgroundClass}`}
          style={{ width: `${value}%` }}
        />
      </div>
    </div>
  );
};

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
  const { settings, support, updateSettings } = useCapture();
  const microphoneOnly = support.supported && support.mode === "microphone_only";
  const suppressQuietAudioWarnings = useAudioWarningStore(
    (state) => state.suppressQuietAudioWarnings,
  );
  const resetWarnings = useAudioWarningStore((state) => state.resetWarnings);
  const [microphones, setMicrophones] = useState<MicrophoneOption[]>([]);
  const [loadingDevices, setLoadingDevices] = useState(false);
  const [deviceError, setDeviceError] = useState<string | null>(null);
  const [previewEnabled, setPreviewEnabled] = useState(false);
  const [previewLevel, setPreviewLevel] = useState(0);
  const [previewRawLevel, setPreviewRawLevel] = useState(0);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const previewContextRef = useRef<AudioContext | null>(null);
  const previewStreamRef = useRef<MediaStream | null>(null);
  const previewGainRef = useRef<GainNode | null>(null);
  const previewAnalyserRef = useRef<AnalyserNode | null>(null);
  const previewRawAnalyserRef = useRef<AnalyserNode | null>(null);
  const previewFrameRef = useRef<number | null>(null);

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

            } catch (error: unknown) {
        if (disposed) {
          return;
        }

        setDeviceError(
          getErrorMessage(error, "Failed to refresh microphone devices."),
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

  useEffect(() => {
    if (!previewGainRef.current) {
      return;
    }
    previewGainRef.current.gain.value = settings.microphoneGain;
  }, [settings.microphoneGain]);

  useEffect(() => {
    if (!previewEnabled) {
      return;
    }

    if (!navigator.mediaDevices?.getUserMedia) {
      setPreviewError(
        "This browser cannot open a microphone preview. Use Chrome or another supported Chromium browser.",
      );
      setPreviewEnabled(false);
      return;
    }

    let cancelled = false;
    let localStream: MediaStream | null = null;

    const stopPreview = async () => {
      if (previewFrameRef.current !== null) {
        cancelAnimationFrame(previewFrameRef.current);
        previewFrameRef.current = null;
      }
      previewGainRef.current = null;
      previewAnalyserRef.current = null;
      previewRawAnalyserRef.current = null;
      if (previewStreamRef.current) {
        previewStreamRef.current.getTracks().forEach((track) => track.stop());
        previewStreamRef.current = null;
      }
      if (previewContextRef.current) {
        await previewContextRef.current.close().catch(() => {});
        previewContextRef.current = null;
      }
      setPreviewLevel(0);
      setPreviewRawLevel(0);
    };

    const startPreview = async () => {
      setPreviewLoading(true);
      setPreviewError(null);
      await stopPreview();

      try {
        localStream = await navigator.mediaDevices.getUserMedia({
          audio: {
            deviceId: settings.microphoneDeviceId
              ? { exact: settings.microphoneDeviceId }
              : undefined,
            echoCancellation: settings.echoCancellation,
            noiseSuppression: settings.noiseSuppression,
            autoGainControl: settings.autoGainControl,
          },
        });

        if (cancelled) {
          localStream.getTracks().forEach((track) => track.stop());
          return;
        }

        const localContext = new AudioContext();
        if (localContext.state === "suspended") {
          await localContext.resume();
        }

        const source = localContext.createMediaStreamSource(localStream);
        const gainNode = localContext.createGain();
        gainNode.gain.value = settings.microphoneGain;
        const rawAnalyser = localContext.createAnalyser();
        const analyser = localContext.createAnalyser();
        rawAnalyser.fftSize = 256;
        analyser.fftSize = 256;
        rawAnalyser.smoothingTimeConstant = 0.8;
        analyser.smoothingTimeConstant = 0.8;

        source.connect(rawAnalyser);
        source.connect(gainNode);
        gainNode.connect(analyser);

        previewContextRef.current = localContext;
        previewStreamRef.current = localStream;
        previewGainRef.current = gainNode;
        previewAnalyserRef.current = analyser;
        previewRawAnalyserRef.current = rawAnalyser;

        const readLevel = (target: AnalyserNode) => {
          const samples = new Uint8Array(target.fftSize);
          target.getByteTimeDomainData(samples);
          let sumSquares = 0;
          for (const sample of samples) {
            const centered = (sample - 128) / 128;
            sumSquares += centered * centered;
          }
          return clampPreviewLevel(Math.sqrt(sumSquares / samples.length) * 180);
        };

        const tick = () => {
          if (cancelled || !previewAnalyserRef.current || !previewRawAnalyserRef.current) {
            return;
          }
          setPreviewRawLevel(readLevel(previewRawAnalyserRef.current));
          setPreviewLevel(readLevel(previewAnalyserRef.current));
          previewFrameRef.current = requestAnimationFrame(tick);
        };

        previewFrameRef.current = requestAnimationFrame(tick);
      } catch (error) {
        if (!cancelled) {
          setPreviewError(
            getErrorMessage(error, "Failed to start the microphone input test."),
          );
          setPreviewEnabled(false);
        }
      } finally {
        if (!cancelled) {
          setPreviewLoading(false);
        }
      }
    };

    void startPreview();

    return () => {
      cancelled = true;
      void stopPreview();
    };
  }, [
    previewEnabled,
    settings.autoGainControl,
    settings.echoCancellation,
    settings.microphoneGain,
    settings.microphoneDeviceId,
    settings.noiseSuppression,
  ]);

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

        } catch (error: unknown) {
      setDeviceError(
        getErrorMessage(error, "Failed to refresh microphone devices."),
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
                {microphoneOnly
                  ? "Select the phone microphone used for mobile recording. Nojoin balances levels during recording."
                  : "Select the microphone added to shared audio. Nojoin balances system and microphone levels during recording."}
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
              description={
                microphoneOnly
                  ? "This input is recorded directly on mobile Chrome."
                  : "This input is mixed with shared tab or system audio during capture."
              }
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
              label="Microphone gain"
              icon={<Volume2 className="h-4 w-4" />}
              description="Adjust the local microphone level mixed into the recording."
            >
              <div className="space-y-3">
                <input
                  type="range"
                  min={GAIN_MIN}
                  max={GAIN_MAX}
                  step={GAIN_STEP}
                  value={settings.microphoneGain}
                  onChange={(event) =>
                    updateSettings({
                      microphoneGain: Number(event.target.value),
                    })
                  }
                  className="w-full accent-orange-500"
                />
                <div className="flex items-center justify-between text-xs text-gray-500 dark:text-gray-400">
                  <span>Quieter</span>
                  <span className="font-semibold text-gray-700 dark:text-gray-200">
                    {formatGainLabel(settings.microphoneGain)}
                  </span>
                  <span>Louder</span>
                </div>
              </div>
            </SettingsField>
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            <SettingsField
              label="Shared-audio gain"
              icon={<Volume2 className="h-4 w-4" />}
              description="Adjust the shared tab or system audio level relative to your microphone."
            >
              <div className="space-y-3">
                <input
                  type="range"
                  min={GAIN_MIN}
                  max={GAIN_MAX}
                  step={GAIN_STEP}
                  value={settings.systemGain}
                  onChange={(event) =>
                    updateSettings({
                      systemGain: Number(event.target.value),
                    })
                  }
                  className="w-full accent-orange-500"
                />
                <div className="flex items-center justify-between text-xs text-gray-500 dark:text-gray-400">
                  <span>Quieter</span>
                  <span className="font-semibold text-gray-700 dark:text-gray-200">
                    {formatGainLabel(settings.systemGain)}
                  </span>
                  <span>Louder</span>
                </div>
              </div>
            </SettingsField>

            <SettingsField
              label="Automatic levels"
              icon={<Volume2 className="h-4 w-4" />}
              description="Nojoin still balances sources during recording, but these sliders now set the baseline mix."
            >
              <div className="rounded-xl border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs font-semibold text-emerald-800 dark:border-emerald-500/20 dark:bg-emerald-500/10 dark:text-emerald-100">
                Enabled with manual baseline gain
              </div>
            </SettingsField>
          </div>

          <SettingsPanel variant="field" className="space-y-4">
            <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <div className="text-sm font-semibold text-gray-900 dark:text-white">
                  Live microphone input test
                </div>
                <p className="mt-1 text-xs leading-5 text-gray-600 dark:text-gray-300">
                  Preview your microphone locally in the browser and adjust the mic gain slider until speech lands comfortably in the meter. Shared-audio gain is still best validated during a short live test recording.
                </p>
              </div>
              <button
                type="button"
                onClick={() => {
                  setPreviewError(null);
                  setPreviewEnabled((current) => !current);
                }}
                disabled={previewLoading}
                className={SECONDARY_BUTTON_STYLES}
              >
                {previewLoading ? (
                  <Loader2 className="h-3 w-3 animate-spin" />
                ) : (
                  <Mic className="h-3 w-3" />
                )}
                {previewEnabled ? "Stop input test" : "Start input test"}
              </button>
            </div>

            <div className="grid gap-4 lg:grid-cols-2">
              <MeterBar label="Raw microphone" value={previewRawLevel} tone="emerald" />
              <MeterBar
                label="After microphone gain"
                value={previewLevel}
                tone="orange"
              />
            </div>

            {previewError ? (
              <SettingsCallout tone="warning" title="Input test unavailable">
                <p className="leading-6">{previewError}</p>
              </SettingsCallout>
            ) : null}

            <div className="rounded-xl border border-gray-200/80 bg-gray-50 px-3 py-2 text-xs leading-5 text-gray-600 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-300">
              Browser processing toggles affect the next preview start and the next recording start or resume. Gain slider changes apply immediately to the live input test and to any active recording.
            </div>
          </SettingsPanel>

          <div className="grid gap-4 lg:grid-cols-3">
            <SettingsField
              label="Echo cancellation"
              icon={<Mic className="h-4 w-4" />}
              description="Helps reduce loopback and speaker bleed for headset and speakerphone use."
            >
              <label className="flex items-center justify-between rounded-xl border border-gray-200/80 bg-gray-50 px-3 py-2 text-sm text-gray-700 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-200">
                <span>{settings.echoCancellation ? "Enabled" : "Disabled"}</span>
                <input
                  type="checkbox"
                  checked={settings.echoCancellation}
                  onChange={(event) =>
                    updateSettings({ echoCancellation: event.target.checked })
                  }
                  className="h-4 w-4 accent-orange-500"
                />
              </label>
            </SettingsField>

            <SettingsField
              label="Noise suppression"
              icon={<Mic className="h-4 w-4" />}
              description="Reduces steady background noise before the mic is mixed into the recording."
            >
              <label className="flex items-center justify-between rounded-xl border border-gray-200/80 bg-gray-50 px-3 py-2 text-sm text-gray-700 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-200">
                <span>{settings.noiseSuppression ? "Enabled" : "Disabled"}</span>
                <input
                  type="checkbox"
                  checked={settings.noiseSuppression}
                  onChange={(event) =>
                    updateSettings({ noiseSuppression: event.target.checked })
                  }
                  className="h-4 w-4 accent-orange-500"
                />
              </label>
            </SettingsField>

            <SettingsField
              label="Browser auto gain"
              icon={<Mic className="h-4 w-4" />}
              description="Lets the browser lift a quiet microphone before Nojoin applies its own balancing."
            >
              <label className="flex items-center justify-between rounded-xl border border-gray-200/80 bg-gray-50 px-3 py-2 text-sm text-gray-700 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-200">
                <span>{settings.autoGainControl ? "Enabled" : "Disabled"}</span>
                <input
                  type="checkbox"
                  checked={settings.autoGainControl}
                  onChange={(event) =>
                    updateSettings({ autoGainControl: event.target.checked })
                  }
                  className="h-4 w-4 accent-orange-500"
                />
              </label>
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
