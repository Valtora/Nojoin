"use client";

import { useEffect, useRef, useState } from "react";
import { useAudioWarningStore } from "@/lib/audioWarningStore";
import { useServiceStatusStore } from "@/lib/serviceStatusStore";

const COMPANION_URL = "http://127.0.0.1:12345";
const HISTORY_LENGTH = 48;
const POLL_INTERVAL_MS = 180;
const CALIBRATION_WINDOW = 72;
const MIN_REFERENCE_RANGE = 2.5;
const NOISE_GATE_FLOOR = 0.35;
const ACTIVITY_LEVEL_THRESHOLD = 6;
const QUIET_HINT_DELAY_MS = 20000;
const ACTIVITY_HINT_COPY = {
  title: "Audio has been quiet for a bit",
  message:
    "That can be normal during a quiet stretch. If the meeting should be active, check the companion audio device settings.",
};

const AUDIO_BAR_CLASS_NAME =
  "bg-gradient-to-t from-orange-600 via-orange-500 to-amber-300";

function combineAudioLevel(inputLevel: number, outputLevel: number) {
  return Math.max(inputLevel, outputLevel);
};

function quantile(values: number[], percentile: number) {
  if (values.length === 0) {
    return 0;
  }

  const sorted = [...values].sort((left, right) => left - right);
  const index = (sorted.length - 1) * percentile;
  const lowerIndex = Math.floor(index);
  const upperIndex = Math.ceil(index);
  if (lowerIndex === upperIndex) {
    return sorted[lowerIndex];
  }

  const weight = index - lowerIndex;
  return (
    sorted[lowerIndex] + (sorted[upperIndex] - sorted[lowerIndex]) * weight
  );
}

function calibrateLevel(rawLevel: number, history: number[]) {
  const baseline = quantile(history, 0.18);
  const gate = Math.max(NOISE_GATE_FLOOR, baseline + 0.25);
  const referencePeak = Math.max(
    quantile(history, 0.92),
    quantile(history, 0.75) * 1.25,
    gate + MIN_REFERENCE_RANGE,
  );

  if (rawLevel <= gate) {
    return 0;
  }

  const normalised = Math.min(
    1,
    Math.max(0, (rawLevel - gate) / (referencePeak - gate)),
  );

  return Math.round(Math.pow(normalised, 0.72) * 100);
}

function smoothLevel(previousLevel: number, nextLevel: number) {
  const riseBlend = nextLevel > previousLevel ? 0.65 : 0.35;
  return Math.round(previousLevel + (nextLevel - previousLevel) * riseBlend);
}

interface AudioLevelsResponse {
  input_level: number;
  output_level: number;
  is_recording: boolean;
}

interface LiveAudioWaveformProps {
  recordingId: number;
  enabled: boolean;
  paused?: boolean;
}

const zeroHistory = () => Array.from({ length: HISTORY_LENGTH }, () => 0);

function WaveformTrack({
  history,
  barClassName,
}: {
  history: number[];
  barClassName: string;
}) {
  return (
    <div className="rounded-3xl border border-white/60 bg-white/75 p-4 shadow-lg shadow-orange-950/10 backdrop-blur dark:border-white/10 dark:bg-gray-950/60 dark:shadow-black/20">
      <div className="flex h-24 items-end gap-1 overflow-hidden rounded-2xl bg-gradient-to-b from-orange-100/80 via-white to-white px-2 py-3 dark:from-orange-500/10 dark:via-gray-950 dark:to-gray-950">
        {history.map((sample, index) => (
          <div
            key={`audio-bar-${index}`}
            className="flex h-full flex-1 items-end justify-center"
          >
            <div
              className={`h-full min-w-[2px] w-[58%] rounded-full transition-[height,opacity] duration-150 ${barClassName}`}
              style={{
                height: `${Math.max(6, sample)}%`,
                opacity: 0.35 + (sample / 140),
              }}
            />
          </div>
        ))}
      </div>
    </div>
  );
}

export default function LiveAudioWaveform({
  recordingId,
  enabled,
  paused = false,
}: LiveAudioWaveformProps) {
  const companionMonitoringEnabled = useServiceStatusStore(
    (state) => state.companionMonitoringEnabled,
  );
  const [audioHistory, setAudioHistory] = useState<number[]>(zeroHistory);
  const [showQuietHint, setShowQuietHint] = useState(false);
  const inputCalibrationHistoryRef = useRef<number[]>([]);
  const outputCalibrationHistoryRef = useRef<number[]>([]);
  const lastAudioActivityAtRef = useRef<number>(Date.now());
  const suppressQuietAudioWarnings = useAudioWarningStore(
    (state) => state.suppressQuietAudioWarnings,
  );
  const dismissedForMeeting = useAudioWarningStore((state) =>
    state.dismissedMeetingRecordingIds.includes(recordingId),
  );
  const dismissForMeeting = useAudioWarningStore(
    (state) => state.dismissForMeeting,
  );
  const suppressWarnings = useAudioWarningStore(
    (state) => state.suppressWarnings,
  );

  useEffect(() => {
    const resetActivityTracking = () => {
      const now = Date.now();
      lastAudioActivityAtRef.current = now;
      setShowQuietHint(false);
    };

    if (!enabled || !companionMonitoringEnabled) {
      setAudioHistory(zeroHistory());
      inputCalibrationHistoryRef.current = [];
      outputCalibrationHistoryRef.current = [];
      resetActivityTracking();
      return;
    }

    resetActivityTracking();

    let cancelled = false;
    let timeoutId: number | undefined;

    const appendSample = (history: number[], nextValue: number) => {
      return [...history.slice(-(HISTORY_LENGTH - 1)), nextValue];
    };

    const pollLevels = async () => {
      try {
        const response = await fetch(`${COMPANION_URL}/levels/live`, {
          method: "GET",
          headers: { "Content-Type": "application/json" },
        });

        if (!response.ok) {
          throw new Error(`Companion returned ${response.status}`);
        }

        const data: AudioLevelsResponse = await response.json();
        const rawInputLevel = Math.max(0, Math.min(100, data.input_level));
        const rawOutputLevel = Math.max(0, Math.min(100, data.output_level));

        if (cancelled) {
          return;
        }

        const nextInputCalibrationHistory = [
          ...inputCalibrationHistoryRef.current,
          rawInputLevel,
        ].slice(-CALIBRATION_WINDOW);
        inputCalibrationHistoryRef.current = nextInputCalibrationHistory;
        const calibratedInputLevel = calibrateLevel(
          rawInputLevel,
          nextInputCalibrationHistory,
        );

        const nextOutputCalibrationHistory = [
          ...outputCalibrationHistoryRef.current,
          rawOutputLevel,
        ].slice(-CALIBRATION_WINDOW);
        outputCalibrationHistoryRef.current = nextOutputCalibrationHistory;
        const calibratedOutputLevel = calibrateLevel(
          rawOutputLevel,
          nextOutputCalibrationHistory,
        );

        const combinedLevel = combineAudioLevel(
          calibratedInputLevel,
          calibratedOutputLevel,
        );
        const now = Date.now();

        if (!paused) {
          if (combinedLevel > ACTIVITY_LEVEL_THRESHOLD) {
            lastAudioActivityAtRef.current = now;
          }

          setShowQuietHint(
            now - lastAudioActivityAtRef.current >= QUIET_HINT_DELAY_MS,
          );
        } else {
          resetActivityTracking();
        }
        setAudioHistory((displayHistory) => {
          const smoothedLevel = smoothLevel(
            displayHistory[displayHistory.length - 1] || 0,
            combinedLevel,
          );
          return appendSample(displayHistory, smoothedLevel);
        });
      } catch {
        if (cancelled) {
          return;
        }

        setAudioHistory((history) => appendSample(history, 0));
      }

      if (!cancelled) {
        timeoutId = window.setTimeout(pollLevels, POLL_INTERVAL_MS);
      }
    };

    void pollLevels();

    return () => {
      cancelled = true;
      if (timeoutId) {
        window.clearTimeout(timeoutId);
      }
    };
  }, [enabled, companionMonitoringEnabled, paused, recordingId]);

  const showActivityHint = Boolean(
    showQuietHint && !suppressQuietAudioWarnings && !dismissedForMeeting,
  );

  return (
    <div className="space-y-3">
      {showActivityHint ? (
        <div className="rounded-3xl border border-orange-200/80 bg-orange-50/80 px-4 py-3 shadow-sm dark:border-orange-500/20 dark:bg-orange-500/10">
          <p className="text-sm font-medium text-orange-950 dark:text-orange-100">
            {ACTIVITY_HINT_COPY.title}
          </p>
          <p className="mt-1 text-xs leading-5 text-orange-800 dark:text-orange-100/80">
            {ACTIVITY_HINT_COPY.message}
          </p>
          <div className="mt-3 flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() => dismissForMeeting(recordingId)}
              className="rounded-full border border-orange-300/90 bg-white/85 px-3 py-1.5 text-xs font-medium text-orange-900 transition-colors hover:bg-white dark:border-orange-400/25 dark:bg-gray-950/40 dark:text-orange-100 dark:hover:bg-gray-950/70"
            >
              Dismiss
            </button>
            <button
              type="button"
              onClick={() => {
                dismissForMeeting(recordingId);
                suppressWarnings();
              }}
              className="rounded-full border border-orange-300/70 px-3 py-1.5 text-xs font-medium text-orange-800 transition-colors hover:bg-orange-100/80 dark:border-orange-400/20 dark:text-orange-100 dark:hover:bg-orange-500/10"
            >
              Don&apos;t show again
            </button>
          </div>
        </div>
      ) : null}
      <WaveformTrack
        history={audioHistory}
        barClassName={AUDIO_BAR_CLASS_NAME}
      />
    </div>
  );
}
