"use client";

import { useEffect, useRef, useState } from "react";
import type { RecordingId } from "@/types";
import { useAudioWarningStore } from "@/lib/audioWarningStore";
import { useCapture } from "@/lib/capture/CaptureProvider";
import { useLiveWaveform } from "@/lib/capture/waveform";

const HISTORY_LENGTH = 48;
const ACTIVITY_LEVEL_THRESHOLD = 6;
const QUIET_HINT_DELAY_MS = 20000;
const ACTIVITY_HINT_COPY = {
  title: "Audio has been quiet for a bit",
  message:
    "That can be normal during a quiet stretch. If the meeting should be active, check the microphone selection and the browser share picker.",
  microphoneOnlyMessage:
    "That can be normal during a quiet stretch. If the meeting should be active, check the microphone permission and keep the phone near the speaker.",
};

const AUDIO_BAR_CLASS_NAME =
  "bg-gradient-to-t from-orange-600 via-orange-500 to-amber-300";

function smoothLevel(previousLevel: number, nextLevel: number) {
  const riseBlend = nextLevel > previousLevel ? 0.65 : 0.35;
  return Math.round(previousLevel + (nextLevel - previousLevel) * riseBlend);
}

interface LiveAudioWaveformProps {
  recordingId: RecordingId;
  enabled: boolean;
  paused?: boolean;
}

const zeroHistory = () => Array.from({ length: HISTORY_LENGTH }, () => 0);

function WaveformTrack({
  history,
  barClassName,
  dynamicMin,
  dynamicMax,
}: {
  history: number[];
  barClassName: string;
  dynamicMin: number;
  dynamicMax: number;
}) {
  const range = dynamicMax - dynamicMin;
  return (
    <div className="rounded-3xl border border-white/60 bg-white/75 p-4 shadow-lg shadow-orange-950/10 backdrop-blur dark:border-white/10 dark:bg-gray-950/60 dark:shadow-black/20">
      <div className="flex h-24 items-end gap-1 overflow-hidden rounded-2xl bg-gradient-to-b from-orange-100/80 via-white to-white px-2 py-3 dark:from-orange-500/10 dark:via-gray-950 dark:to-gray-950">
        {history.map((sample, index) => {
          const scaled = range > 0 ? Math.max(0, Math.min(100, ((sample - dynamicMin) / range) * 100)) : 0;
          return (
            <div
              key={`audio-bar-${index}`}
              className="flex h-full flex-1 items-end justify-center"
            >
              <div
                className={`h-full min-w-[2px] w-[58%] rounded-full transition-[height,opacity] duration-150 ${barClassName}`}
                style={{
                  height: `${Math.max(6, scaled)}%`,
                  opacity: 0.35 + (scaled / 140),
                }}
              />
            </div>
          );
        })}
      </div>
    </div>
  );
}

export default function LiveAudioWaveform({
  recordingId,
  enabled,
  paused = false,
}: LiveAudioWaveformProps) {
  const { controller, runtimeActive, support } = useCapture();
  const levels = useLiveWaveform(controller);
  const microphoneOnly = support.supported && support.mode === "microphone_only";
  const [audioHistory, setAudioHistory] = useState<number[]>(zeroHistory);
  const [showQuietHint, setShowQuietHint] = useState(false);
  const lastAudioActivityAtRef = useRef<number>(0);
  const dynamicMinRef = useRef<number>(0);
  const dynamicMaxRef = useRef<number>(20);
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

    const appendSample = (history: number[], nextValue: number) => {
      return [...history.slice(-(HISTORY_LENGTH - 1)), nextValue];
    };

    if (!enabled) {
      setAudioHistory(zeroHistory());
      resetActivityTracking();
      dynamicMinRef.current = 0;
      dynamicMaxRef.current = 20;
      return;
    }

    if (!runtimeActive) {
      setAudioHistory((history) => appendSample(history, 0));
      resetActivityTracking();
      dynamicMinRef.current = 0;
      dynamicMaxRef.current = 20;
      return;
    }

    if (lastAudioActivityAtRef.current === 0) {
      lastAudioActivityAtRef.current = Date.now();
    }

    const combinedLevel = levels.mixed;
    const now = Date.now();

    if (!paused) {
      if (combinedLevel > ACTIVITY_LEVEL_THRESHOLD) {
        lastAudioActivityAtRef.current = now;
      }

      setShowQuietHint(now - lastAudioActivityAtRef.current >= QUIET_HINT_DELAY_MS);

      // Adapt dynamic min and max to signal levels smoothly
      if (combinedLevel > dynamicMaxRef.current) {
        dynamicMaxRef.current = dynamicMaxRef.current + (combinedLevel - dynamicMaxRef.current) * 0.3;
      } else {
        dynamicMaxRef.current = Math.max(20, dynamicMaxRef.current - 0.1);
      }

      if (combinedLevel < dynamicMinRef.current) {
        dynamicMinRef.current = dynamicMinRef.current - (dynamicMinRef.current - combinedLevel) * 0.3;
      } else {
        dynamicMinRef.current = Math.min(5, dynamicMinRef.current + 0.05);
      }
    } else {
      resetActivityTracking();
      dynamicMinRef.current = 0;
      dynamicMaxRef.current = 20;
    }

    setAudioHistory((displayHistory) => {
      const smoothedLevel = smoothLevel(
        displayHistory[displayHistory.length - 1] || 0,
        combinedLevel,
      );
      return appendSample(displayHistory, smoothedLevel);
    });
  }, [
    enabled,
    levels.mixed,
    paused,
    runtimeActive,
  ]);

  const showActivityHint = Boolean(
    showQuietHint &&
      !suppressQuietAudioWarnings &&
      !dismissedForMeeting,
  );

  return (
    <div className="space-y-3">
      {showActivityHint ? (
        <div className="rounded-3xl border border-orange-200/80 bg-orange-50/80 px-4 py-3 shadow-sm dark:border-orange-500/20 dark:bg-orange-500/10">
          <p className="text-sm font-medium text-orange-950 dark:text-orange-100">
            {ACTIVITY_HINT_COPY.title}
          </p>
          <p className="mt-1 text-xs leading-5 text-orange-800 dark:text-orange-100/80">
            {microphoneOnly
              ? ACTIVITY_HINT_COPY.microphoneOnlyMessage
              : ACTIVITY_HINT_COPY.message}
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
        dynamicMin={dynamicMinRef.current}
        dynamicMax={dynamicMaxRef.current}
      />
    </div>
  );
}
