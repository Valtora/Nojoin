"use client";

import { ButtonBase } from '@/components/ui/nativeBases';
import { useEffect, useRef, useState, type RefObject } from "react";
import type { RecordingId } from "@/types";
import { useAudioWarningStore } from "@/lib/audioWarningStore";
import { useCapture } from "@/lib/capture/CaptureProvider";
import { useOnDepsChange } from "@/lib/useOnDepsChange";

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
  "bg-action";

function smoothLevel(previousLevel: number, nextLevel: number) {
  const riseBlend = nextLevel > previousLevel ? 0.65 : 0.35;
  return Math.round(previousLevel + (nextLevel - previousLevel) * riseBlend);
}

interface LiveAudioWaveformProps {
  recordingId: RecordingId;
  enabled: boolean;
  paused?: boolean;
  /** Short track for the recording workspace's toolbar row. */
  compact?: boolean;
  /**
   * Changes whenever new live transcript text arrives. Text arriving shows the
   * audio is getting through, so it holds off the quiet notice as sound does.
   */
  liveTextMark?: string | null;
}

const zeroHistory = () => Array.from({ length: HISTORY_LENGTH }, () => 0);

/** A bar's height and opacity for one sample on the current scale. */
function barStyle(sample: number, dynamicMin: number, dynamicMax: number) {
  const range = dynamicMax - dynamicMin;
  const scaled = range > 0 ? Math.max(0, Math.min(100, ((sample - dynamicMin) / range) * 100)) : 0;
  return { height: `${Math.max(6, scaled)}%`, opacity: String(0.35 + (scaled / 140)) };
}

const RESTING_BAR = barStyle(0, 0, 20);
const BAR_KEYS = Array.from({ length: HISTORY_LENGTH }, (_, index) => `audio-bar-${index}`);

/**
 * The bars, drawn once. The meter writes each sample's heights straight to
 * them, so a sample costs no render: one every animation frame would keep
 * the page's navigations from finishing (issue #822).
 */
function WaveformTrack({
  barRefs,
  barClassName,
  compact = false,
}: {
  barRefs: RefObject<(HTMLDivElement | null)[]>;
  barClassName: string;
  compact?: boolean;
}) {
  return (
    // One surface: the track sits directly in the card that contains it, with
    // no fill, border or float shadow of its own.
    <div
      className={`flex items-end gap-px overflow-hidden rounded-surface-panel bg-surface-inset px-2 sm:gap-1 ${
        compact ? "h-14 py-2" : "h-24 py-3"
      }`}
    >
        {BAR_KEYS.map((key, index) => (
          <div
            key={key}
            className="flex h-full flex-1 items-end justify-center"
          >
            <div
              ref={(element) => {
                barRefs.current[index] = element;
              }}
              data-testid="audio-bar"
              className={`h-full min-w-[2px] w-[58%] rounded-full transition-[height,opacity] duration-150 ${barClassName}`}
              style={RESTING_BAR}
            />
          </div>
        ))}
    </div>
  );
}

export default function LiveAudioWaveform({
  recordingId,
  enabled,
  paused = false,
  compact = false,
  liveTextMark = null,
}: LiveAudioWaveformProps) {
  const { controller, runtimeActive, support } = useCapture();
  const microphoneOnly = support.supported && support.mode === "microphone_only";
  const [showQuietHint, setShowQuietHint] = useState(false);
  const barRefs = useRef<(HTMLDivElement | null)[]>([]);
  const historyRef = useRef<number[]>(zeroHistory());
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

  // The reducer over the level feed: every sample folds into the history and
  // scale, kept in refs, and is drawn on the bars directly. Only the quiet
  // hint is React state, and it changes when the audio goes quiet or comes back.
  useEffect(() => {
    const draw = () => {
      historyRef.current.forEach((sample, index) => {
        const bar = barRefs.current[index];
        if (!bar) return;
        const style = barStyle(sample, dynamicMinRef.current, dynamicMaxRef.current);
        bar.style.height = style.height;
        bar.style.opacity = style.opacity;
      });
    };
    const appendSample = (nextValue: number) => {
      historyRef.current = [...historyRef.current.slice(-(HISTORY_LENGTH - 1)), nextValue];
    };
    const resetActivityTracking = () => {
      lastAudioActivityAtRef.current = Date.now();
      setShowQuietHint(false);
    };
    const resetScale = () => {
      dynamicMinRef.current = 0;
      dynamicMaxRef.current = 20;
    };

    if (!enabled) {
      historyRef.current = zeroHistory();
      resetActivityTracking();
      resetScale();
      draw();
      return;
    }

    if (!runtimeActive) {
      appendSample(0);
      resetActivityTracking();
      resetScale();
      draw();
      return;
    }

    let lastMixed: number | null = null;
    const onSample = (combinedLevel: number) => {
      // A sample only moves the meter when the mixed level changes.
      if (combinedLevel === lastMixed) return;
      lastMixed = combinedLevel;
      if (lastAudioActivityAtRef.current === 0) {
        lastAudioActivityAtRef.current = Date.now();
      }
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
        resetScale();
      }

      const history = historyRef.current;
      appendSample(smoothLevel(history[history.length - 1] || 0, combinedLevel));
      draw();
    };

    onSample(controller.getState().levels.mixed);
    return controller.subscribe((state) => onSample(state.levels.mixed));
  }, [controller, enabled, paused, runtimeActive]);

  // New live text counts as activity: it restarts the quiet timer and drops the hint.
  useOnDepsChange([liveTextMark], () => {
    if (liveTextMark) setShowQuietHint(false);
  });
  useEffect(() => {
    if (liveTextMark) lastAudioActivityAtRef.current = Date.now();
  }, [liveTextMark]);

  const showActivityHint = Boolean(
    showQuietHint &&
      !suppressQuietAudioWarnings &&
      !dismissedForMeeting,
  );

  return (
    <div className="space-y-3">
      {showActivityHint ? (
        <div className="rounded-3xl border border-action-border bg-action-tint px-4 py-3 shadow-card">
          <p className="text-sm font-medium text-action-text">
            {ACTIVITY_HINT_COPY.title}
          </p>
          <p className="mt-1 text-xs leading-5 text-action-text">
            {microphoneOnly
              ? ACTIVITY_HINT_COPY.microphoneOnlyMessage
              : ACTIVITY_HINT_COPY.message}
          </p>
          <div className="mt-3 flex flex-wrap gap-2">
            <ButtonBase
              data-ui-fill="card"
              data-ui-hover="card"
              data-ui-border="full" chrome="action"
              type="button"
              onClick={() => dismissForMeeting(recordingId)}
              className="rounded-pill border border-action-border bg-surface-card px-3 py-1.5 text-xs font-medium text-action-text transition-colors hover:bg-surface-card"
            >
              Dismiss
            </ButtonBase>
            <ButtonBase
              data-ui-hover="action-tint"
              data-ui-border="full" chrome="action"
              type="button"
              onClick={() => {
                dismissForMeeting(recordingId);
                suppressWarnings();
              }}
              className="rounded-pill border border-action-border px-3 py-1.5 text-xs font-medium text-action-text transition-colors hover:bg-action-tint"
            >
              Don&apos;t show again
            </ButtonBase>
          </div>
        </div>
      ) : null}
      <WaveformTrack
        barRefs={barRefs}
        barClassName={AUDIO_BAR_CLASS_NAME}
        compact={compact}
      />
    </div>
  );
}
