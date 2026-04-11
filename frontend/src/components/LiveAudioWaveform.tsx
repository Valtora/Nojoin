"use client";

import { useEffect, useRef, useState } from "react";

const COMPANION_URL = "http://127.0.0.1:12345";
const HISTORY_LENGTH = 48;
const POLL_INTERVAL_MS = 180;
const CALIBRATION_WINDOW = 72;
const MIN_REFERENCE_RANGE = 2.5;
const NOISE_GATE_FLOOR = 0.35;

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
  enabled: boolean;
  paused?: boolean;
}

const zeroHistory = () => Array.from({ length: HISTORY_LENGTH }, () => 0);

function WaveformTrack({
  label,
  value,
  history,
  barClassName,
}: {
  label: string;
  value: number;
  history: number[];
  barClassName: string;
}) {
  return (
    <div className="rounded-3xl border border-white/60 bg-white/75 p-4 shadow-lg shadow-orange-950/10 backdrop-blur dark:border-white/10 dark:bg-gray-950/60 dark:shadow-black/20">
      <div className="mb-3 flex items-center justify-between gap-4">
        <span className="text-sm font-medium text-gray-700 dark:text-gray-200">
          {label}
        </span>
        <span className="text-xs font-semibold uppercase tracking-[0.22em] text-gray-500 dark:text-gray-400">
          {value}%
        </span>
      </div>
      <div className="flex h-24 items-end gap-1 overflow-hidden rounded-2xl bg-gradient-to-b from-orange-100/80 via-white to-white px-2 py-3 dark:from-orange-500/10 dark:via-gray-950 dark:to-gray-950">
        {history.map((sample, index) => (
          <div
            key={`${label}-${index}`}
            className={`flex-1 rounded-full transition-[height,opacity] duration-150 ${barClassName}`}
            style={{
              height: `${Math.max(6, sample)}%`,
              opacity: 0.35 + (sample / 140),
            }}
          />
        ))}
      </div>
    </div>
  );
}

export default function LiveAudioWaveform({
  enabled,
  paused = false,
}: LiveAudioWaveformProps) {
  const [inputHistory, setInputHistory] = useState<number[]>(zeroHistory);
  const [outputHistory, setOutputHistory] = useState<number[]>(zeroHistory);
  const [inputLevel, setInputLevel] = useState(0);
  const [outputLevel, setOutputLevel] = useState(0);
  const inputCalibrationHistoryRef = useRef<number[]>([]);
  const outputCalibrationHistoryRef = useRef<number[]>([]);

  useEffect(() => {
    if (!enabled) {
      setInputHistory(zeroHistory());
      setOutputHistory(zeroHistory());
      setInputLevel(0);
      setOutputLevel(0);
      inputCalibrationHistoryRef.current = [];
      outputCalibrationHistoryRef.current = [];
      return;
    }

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
        setInputLevel((previousLevel) =>
          smoothLevel(previousLevel, calibratedInputLevel),
        );
        setInputHistory((displayHistory) => {
          const smoothedLevel = smoothLevel(
            displayHistory[displayHistory.length - 1] || 0,
            calibratedInputLevel,
          );
          return appendSample(displayHistory, smoothedLevel);
        });

        const nextOutputCalibrationHistory = [
          ...outputCalibrationHistoryRef.current,
          rawOutputLevel,
        ].slice(-CALIBRATION_WINDOW);
        outputCalibrationHistoryRef.current = nextOutputCalibrationHistory;
        const calibratedOutputLevel = calibrateLevel(
          rawOutputLevel,
          nextOutputCalibrationHistory,
        );
        setOutputLevel((previousLevel) =>
          smoothLevel(previousLevel, calibratedOutputLevel),
        );
        setOutputHistory((displayHistory) => {
          const smoothedLevel = smoothLevel(
            displayHistory[displayHistory.length - 1] || 0,
            calibratedOutputLevel,
          );
          return appendSample(displayHistory, smoothedLevel);
        });
      } catch {
        if (cancelled) {
          return;
        }

        setInputLevel(0);
        setOutputLevel(0);
        setInputHistory((history) => appendSample(history, 0));
        setOutputHistory((history) => appendSample(history, 0));
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
  }, [enabled]);

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between gap-3 text-xs uppercase tracking-[0.24em] text-gray-500 dark:text-gray-400">
        <span>Live Audio Levels</span>
        <span>{paused ? "Paused" : "Monitoring"}</span>
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        <WaveformTrack
          label="System Audio"
          value={outputLevel}
          history={outputHistory}
          barClassName="bg-gradient-to-t from-orange-600 via-orange-500 to-amber-300"
        />
        <WaveformTrack
          label="Microphone"
          value={inputLevel}
          history={inputHistory}
          barClassName="bg-gradient-to-t from-rose-600 via-orange-500 to-orange-200"
        />
      </div>
    </div>
  );
}
