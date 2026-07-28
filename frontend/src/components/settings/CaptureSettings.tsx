"use client";

import { useEffect, useRef, useState } from "react";
import { Loader2, Mic, RefreshCw, Volume2 } from "lucide-react";

import { useCapture } from "@/lib/capture/CaptureProvider";
import { getErrorMessage } from "@/lib/errors";

import SettingsBlock from "./SettingsBlock";
import SettingsCallout from "./SettingsCallout";
import SettingsCard from "./SettingsCard";
import SettingsRow from "./SettingsRow";
import SettingsStatusBadge from "./SettingsStatusBadge";
import {
  SETTINGS_BUTTON_SECONDARY,
  SETTINGS_SELECT_CLASS,
} from "./settingsControls";

interface MicrophoneOption {
  deviceId: string;
  label: string;
}

const GAIN_MIN = 0;
const GAIN_MAX = 3;
const GAIN_STEP = 0.05;

const clampPreviewLevel = (value: number) =>
  Math.max(0, Math.min(100, Math.round(value)));

const formatGainLabel = (value: number) => `${value.toFixed(2)}x`;

/** The microphone and shared-audio sliders are identical but for their value. */
const GainSlider = ({
  value,
  onChange,
  label,
}: {
  value: number;
  onChange: (value: number) => void;
  label: string;
}) => (
  <div className="space-y-2">
    <input
      type="range"
      aria-label={label}
      min={GAIN_MIN}
      max={GAIN_MAX}
      step={GAIN_STEP}
      value={value}
      onChange={(event) => onChange(Number(event.target.value))}
      className="w-full accent-orange-500"
    />
    <div className="flex items-center justify-between text-xs contrast-helper">
      <span>Quieter</span>
      <span className="font-semibold text-gray-700 dark:text-gray-200">
        {formatGainLabel(value)}
      </span>
      <span>Louder</span>
    </div>
  </div>
);

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

/**
 * Microphone selection and levels.
 *
 * The browser processing toggles and the quiet-audio reminders that used to
 * share this component now live in CaptureProcessingSettings, because they sit
 * behind the Advanced gate while device and level selection does not.
 */
export default function CaptureSettings() {
  const { settings, support, updateSettings } = useCapture();
  const microphoneOnly = support.supported && support.mode === "microphone_only";
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

  return (
    <SettingsCard
      title="Input"
      description={
        microphoneOnly
          ? "The phone microphone used for mobile recording. Nojoin balances levels while recording."
          : "The microphone added to shared audio. Nojoin balances system and microphone levels while recording."
      }
      headerAside={
        <button
          type="button"
          onClick={() => void refreshDevices()}
          disabled={loadingDevices}
          className={SETTINGS_BUTTON_SECONDARY}
        >
          {loadingDevices ? (
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
          ) : (
            <RefreshCw className="h-4 w-4" aria-hidden="true" />
          )}
          Refresh devices
        </button>
      }
    >
      {deviceError ? (
        <SettingsBlock>
          <SettingsCallout tone="warning" title="Microphone list unavailable">
            <p className="leading-6">{deviceError}</p>
          </SettingsCallout>
        </SettingsBlock>
      ) : null}

      <SettingsRow
        id="recording-microphone"
        label="Microphone"
        description={
          microphoneOnly
            ? "Recorded directly on mobile Chrome."
            : "Mixed with shared tab or system audio during capture."
        }
        icon={<Mic className="h-4 w-4 contrast-icon-muted" aria-hidden="true" />}
      >
        <select
          value={settings.microphoneDeviceId || ""}
          onChange={(event) =>
            updateSettings({
              microphoneDeviceId: event.target.value || null,
            })
          }
          className={SETTINGS_SELECT_CLASS}
        >
          <option value="">System default</option>
          {microphones.map((device) => (
            <option key={device.deviceId} value={device.deviceId}>
              {device.label}
            </option>
          ))}
        </select>
      </SettingsRow>

      <SettingsRow
        id="recording-microphone-gain"
        label="Microphone gain"
        description="The local microphone level mixed into the recording."
        icon={<Volume2 className="h-4 w-4 contrast-icon-muted" aria-hidden="true" />}
      >
        <GainSlider
          value={settings.microphoneGain}
          onChange={(microphoneGain) => updateSettings({ microphoneGain })}
          label="Microphone gain"
        />
      </SettingsRow>

      <SettingsRow
        id="recording-shared-audio-gain"
        label="Shared-audio gain"
        description="The shared tab or system audio level, relative to your microphone."
        icon={<Volume2 className="h-4 w-4 contrast-icon-muted" aria-hidden="true" />}
      >
        <GainSlider
          value={settings.systemGain}
          onChange={(systemGain) => updateSettings({ systemGain })}
          label="Shared-audio gain"
        />
      </SettingsRow>

      <SettingsRow
        id="recording-automatic-levels"
        label="Automatic levels"
        description="Nojoin balances sources while recording. The sliders above set the baseline mix it starts from."
        icon={<Volume2 className="h-4 w-4 contrast-icon-muted" aria-hidden="true" />}
        controlClassName="sm:min-w-0 sm:flex sm:justify-end"
      >
        <SettingsStatusBadge tone="success">
          Enabled with manual baseline
        </SettingsStatusBadge>
      </SettingsRow>

      <SettingsBlock
        id="recording-input-test"
        label="Live microphone input test"
        description="Preview your microphone locally and raise the gain until speech lands comfortably in the meter. Shared-audio gain is best checked during a short test recording."
        aside={
          <button
            type="button"
            onClick={() => {
              setPreviewError(null);
              setPreviewEnabled((current) => !current);
            }}
            disabled={previewLoading}
            className={SETTINGS_BUTTON_SECONDARY}
          >
            {previewLoading ? (
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            ) : (
              <Mic className="h-4 w-4" aria-hidden="true" />
            )}
            {previewEnabled ? "Stop input test" : "Start input test"}
          </button>
        }
        inset
        contentClassName="space-y-4"
      >
        <div className="grid gap-4 sm:grid-cols-2">
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
      </SettingsBlock>
    </SettingsCard>
  );
}
