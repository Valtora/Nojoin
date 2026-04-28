"use client";

import { useEffect, useRef, useState } from "react";
import { Pause, Play, Square } from "lucide-react";

import { useServiceStatusStore } from "@/lib/serviceStatusStore";
import {
  COMPANION_LOCAL_CONNECTION_UNAVAILABLE_MESSAGE,
  companionLocalFetch,
  isCompanionLocalConnectionError,
  readCompanionLocalError,
  type CompanionLocalAction,
} from "@/lib/companionLocalApi";

const COMPANION_COMMAND_ACTIONS: Record<string, CompanionLocalAction> = {
  stop: "recording:stop",
  pause: "recording:pause",
  resume: "recording:resume",
};

interface LiveMeetingControlsProps {
  size?: "compact" | "full";
  onMeetingEnd?: () => void;
}

function formatTime(seconds: number) {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  if (h > 0) {
    return `${h}:${m.toString().padStart(2, "0")}:${s.toString().padStart(2, "0")}`;
  }
  return `${m.toString().padStart(2, "0")}:${s.toString().padStart(2, "0")}`;
}

export default function LiveMeetingControls({
  size = "full",
  onMeetingEnd,
}: LiveMeetingControlsProps) {
  const {
    companion,
    companionStatus,
    companionLocalHttpsStatus,
    recordingDuration,
    checkCompanion,
    enableCompanionMonitoring,
  } = useServiceStatusStore();

  const [elapsedTime, setElapsedTime] = useState<number>(0);
  const [error, setError] = useState<string | null>(null);
  const timerRef = useRef<NodeJS.Timeout | null>(null);
  const localHttpsNeedsRepair = companionLocalHttpsStatus === "needs-repair";
  const isRecording = companionStatus === "recording";

  useEffect(() => {
    setElapsedTime(recordingDuration);
  }, [recordingDuration]);

  useEffect(() => {
    if (companion && isRecording) {
      timerRef.current = setInterval(() => {
        setElapsedTime((current) => current + 1);
      }, 1000);
    } else if (timerRef.current) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [companion, isRecording]);

  const sendCommand = async (command: "stop" | "pause" | "resume") => {
    setError(null);
    if (localHttpsNeedsRepair) {
      setError(
        "Browser repair required. Open Companion Settings, then follow the repair steps in the Companion app.",
      );
      return null;
    }

    try {
      const action = COMPANION_COMMAND_ACTIONS[command];
      const res = await companionLocalFetch(
        `/${command}`,
        { method: "POST", headers: { "Content-Type": "application/json" } },
        action,
      );

      if (!res.ok) {
        const errorMessage = await readCompanionLocalError(
          res,
          `Companion App error: ${res.status}`,
        );
        setError(errorMessage || "Failed to reach Backend API from Companion App.");
        return null;
      }

      enableCompanionMonitoring();
      setTimeout(() => checkCompanion(), 500);
      return await res.json();
    } catch (err: unknown) {
      if (isCompanionLocalConnectionError(err)) {
        setError(COMPANION_LOCAL_CONNECTION_UNAVAILABLE_MESSAGE);
      } else if (err instanceof Error && err.message) {
        setError(err.message);
      } else {
        setError("Failed to connect to Companion App.");
      }
      console.error(err);
      return null;
    }
  };

  const handleStop = async () => {
    const result = await sendCommand("stop");
    if (result) {
      setElapsedTime(0);
      if (onMeetingEnd) {
        setTimeout(onMeetingEnd, 1000);
      }
    }
  };

  const statusLabel = isRecording ? "Recording" : "Paused";

  if (size === "compact") {
    return (
      <div className="space-y-2">
        {error && (
          <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700 dark:border-red-500/20 dark:bg-red-500/10 dark:text-red-300">
            {error}
          </div>
        )}
        <div className="flex items-center gap-2">
          <div className="flex flex-1 items-center gap-2 rounded-xl border border-red-100 bg-red-50 px-3 py-2 text-red-700 dark:border-red-500/20 dark:bg-red-500/10 dark:text-red-300">
            <span
              className={`inline-block h-2 w-2 rounded-full bg-red-500 ${isRecording ? "animate-pulse" : ""}`}
            />
            <span className="text-xs font-semibold uppercase tracking-[0.14em]">
              {statusLabel}
            </span>
            <span className="ml-auto font-mono text-sm font-semibold text-gray-950 dark:text-white">
              {formatTime(elapsedTime)}
            </span>
          </div>
          {isRecording ? (
            <button
              type="button"
              onClick={() => sendCommand("pause")}
              disabled={localHttpsNeedsRepair}
              className="rounded-xl border border-gray-300 bg-white p-2 text-gray-700 transition-colors hover:border-orange-300 hover:text-orange-700 disabled:cursor-not-allowed disabled:opacity-50 dark:border-gray-700 dark:bg-gray-950/60 dark:text-gray-200 dark:hover:border-orange-500/30 dark:hover:text-orange-300"
              title="Pause recording"
              aria-label="Pause recording"
            >
              <Pause className="h-4 w-4" />
            </button>
          ) : (
            <button
              type="button"
              onClick={() => sendCommand("resume")}
              disabled={localHttpsNeedsRepair}
              className="rounded-xl border border-gray-300 bg-white p-2 text-gray-700 transition-colors hover:border-orange-300 hover:text-orange-700 disabled:cursor-not-allowed disabled:opacity-50 dark:border-gray-700 dark:bg-gray-950/60 dark:text-gray-200 dark:hover:border-orange-500/30 dark:hover:text-orange-300"
              title="Resume recording"
              aria-label="Resume recording"
            >
              <Play className="h-4 w-4" />
            </button>
          )}
          <button
            type="button"
            onClick={handleStop}
            disabled={localHttpsNeedsRepair}
            className="rounded-xl bg-red-600 p-2 text-white transition-colors hover:bg-red-700 disabled:cursor-not-allowed disabled:opacity-50"
            title="Stop recording"
            aria-label="Stop recording"
          >
            <Square className="h-4 w-4 fill-current" />
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {error && (
        <div className="rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-500/20 dark:bg-red-500/10 dark:text-red-300">
          {error}
        </div>
      )}
      <div className="flex items-center justify-between gap-4 rounded-[1.5rem] border border-red-100 bg-red-50 px-4 py-4 text-red-700 dark:border-red-500/20 dark:bg-red-500/10 dark:text-red-300">
        <div className="flex items-center gap-3">
          <div
            className={`h-2.5 w-2.5 rounded-full bg-red-500 ${isRecording ? "animate-pulse" : ""}`}
          />
          <span className="text-sm font-semibold uppercase tracking-[0.16em]">
            {statusLabel}
          </span>
        </div>
        <span className="font-mono text-3xl font-semibold text-gray-950 dark:text-white">
          {formatTime(elapsedTime)}
        </span>
      </div>

      <div className="flex flex-wrap gap-3">
        {isRecording ? (
          <button
            type="button"
            onClick={() => sendCommand("pause")}
            disabled={localHttpsNeedsRepair}
            className="inline-flex flex-1 items-center justify-center gap-2 rounded-2xl border border-gray-300 bg-white px-4 py-3 text-sm font-semibold text-gray-700 transition-colors hover:border-orange-300 hover:text-orange-700 disabled:cursor-not-allowed disabled:opacity-50 dark:border-gray-700 dark:bg-gray-950/60 dark:text-gray-200 dark:hover:border-orange-500/30 dark:hover:text-orange-300"
            title="Pause recording"
          >
            <Pause className="h-4 w-4" />
            Pause
          </button>
        ) : (
          <button
            type="button"
            onClick={() => sendCommand("resume")}
            disabled={localHttpsNeedsRepair}
            className="inline-flex flex-1 items-center justify-center gap-2 rounded-2xl border border-gray-300 bg-white px-4 py-3 text-sm font-semibold text-gray-700 transition-colors hover:border-orange-300 hover:text-orange-700 disabled:cursor-not-allowed disabled:opacity-50 dark:border-gray-700 dark:bg-gray-950/60 dark:text-gray-200 dark:hover:border-orange-500/30 dark:hover:text-orange-300"
            title="Resume recording"
          >
            <Play className="h-4 w-4" />
            Resume
          </button>
        )}

        <button
          type="button"
          onClick={handleStop}
          disabled={localHttpsNeedsRepair}
          className="inline-flex flex-1 items-center justify-center gap-2 rounded-2xl bg-red-600 px-4 py-3 text-sm font-semibold text-white transition-colors hover:bg-red-700 disabled:cursor-not-allowed disabled:opacity-50"
          title="Stop recording"
        >
          <Square className="h-4 w-4 fill-current" />
          Stop
        </button>
      </div>
    </div>
  );
}
