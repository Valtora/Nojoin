"use client";

import { useState, useEffect, useRef } from 'react';
import { Square, Pause, Mic, Circle } from 'lucide-react';
import { useRouter } from 'next/navigation';
import { useServiceStatusStore } from '@/lib/serviceStatusStore';
import {
  getCompanionSteadyStateGuidance,
} from '@/lib/companionSteadyState';
import {
  COMPANION_LOCAL_CONNECTION_UNAVAILABLE_MESSAGE,
  companionLocalFetch,
  isCompanionLocalConnectionError,
  readCompanionLocalError,
  type CompanionLocalAction,
} from '@/lib/companionLocalApi';

interface MeetingControlsProps {
  onMeetingEnd?: () => void;
  variant?: "sidebar" | "dashboard";
}

const COMPANION_COMMAND_ACTIONS: Record<string, CompanionLocalAction> = {
  start: 'recording:start',
  stop: 'recording:stop',
  pause: 'recording:pause',
  resume: 'recording:resume',
};

interface MeetingSurfaceState {
  statusLabel: string;
  message: string;
  nextStepLabel?: string;
  nextStepMessage?: string;
  buttonLabel: string;
  buttonMode: 'start' | 'open-support' | 'wait';
  buttonDisabled: boolean;
}

export default function MeetingControls({
  onMeetingEnd,
  variant = "sidebar",
}: MeetingControlsProps) {
  const {
    backend,
    backendVersion,
    companion,
    companionAuthenticated,
    companionLocalConnectionUnavailable,
    companionLocalHttpsStatus,
    companionStatus,
    companionVersion,
    recordingDuration,
    checkCompanion,
    enableCompanionMonitoring,
  } = useServiceStatusStore();
  
  // Local state for smooth timer, synced with store
  const [elapsedTime, setElapsedTime] = useState<number>(0);
  const timerRef = useRef<NodeJS.Timeout | null>(null);
  const [error, setError] = useState<string | null>(null);
  const router = useRouter();
  const localHttpsNeedsRepair = companionLocalHttpsStatus === 'needs-repair';
  const hasLiveRecording =
    companion &&
    (companionStatus === 'recording' || companionStatus === 'paused');
  const isCompanionUploading = companion && companionStatus === 'uploading';
  const companionGuidance = getCompanionSteadyStateGuidance({
    companion,
    companionAuthenticated,
    companionLocalConnectionUnavailable,
    companionLocalHttpsStatus,
    companionStatus,
    backendVersion,
    companionVersion,
  });
  const meetingSurfaceState: MeetingSurfaceState = !backend
    ? {
        statusLabel: 'Nojoin unavailable',
        message: 'Nojoin is offline right now.',
        nextStepLabel: 'Wait for Nojoin',
        nextStepMessage:
          'Wait for the backend to reconnect before starting another meeting.',
        buttonLabel: 'Nojoin unavailable',
        buttonMode: 'wait',
        buttonDisabled: true,
      }
    : companionGuidance.key === 'browser-repair-required' ||
        companionGuidance.key === 'version-mismatch' ||
        companionGuidance.key === 'not-paired' ||
        companionGuidance.key === 'temporarily-disconnected' ||
        companionGuidance.key === 'browser-repair-in-progress' ||
        companionGuidance.key === 'companion-needs-attention'
      ? {
          statusLabel: companionGuidance.statusLabel,
          message: companionGuidance.summary,
          nextStepLabel: companionGuidance.nextStepLabel,
          nextStepMessage: companionGuidance.nextStepMessage,
          buttonLabel: 'Open Companion support',
          buttonMode: 'open-support',
          buttonDisabled: false,
        }
      : isCompanionUploading
        ? {
            statusLabel: 'Connected',
            message: 'The Companion is still uploading the previous meeting.',
            nextStepLabel: 'Wait for upload',
            nextStepMessage:
              'Wait for the current upload to finish before starting another meeting.',
            buttonLabel: 'Finishing upload...',
            buttonMode: 'wait',
            buttonDisabled: true,
          }
        : companionStatus === 'backend-offline'
          ? {
              statusLabel: 'Connected',
              message: 'The Companion is paired, but Nojoin is offline right now.',
              nextStepLabel: 'Wait for Nojoin',
              nextStepMessage:
                'Wait for the backend to reconnect before starting another meeting.',
              buttonLabel: 'Nojoin unavailable',
              buttonMode: 'wait',
              buttonDisabled: true,
            }
          : {
              statusLabel: 'Connected',
              message: 'Ready to start a meeting.',
              buttonLabel: 'Start Meeting',
              buttonMode: 'start',
              buttonDisabled: false,
            };

  // Sync local timer with store duration
  useEffect(() => {
    setElapsedTime(recordingDuration);
  }, [recordingDuration]);

  // Timer logic
  useEffect(() => {
    if (companion && companionStatus === 'recording') {
      timerRef.current = setInterval(() => {
        setElapsedTime(prev => prev + 1);
      }, 1000);
    } else {
      if (timerRef.current) {
        clearInterval(timerRef.current);
        timerRef.current = null;
      }
    }
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [companion, companionStatus]);

  const formatTime = (seconds: number) => {
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = seconds % 60;
    if (h > 0) {
        return `${h}:${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
    }
    return `${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
  };

  const sendCommand = async (command: string, body?: any) => {
    setError(null);
    if (localHttpsNeedsRepair) {
      setError(
        'Browser repair required. Open Companion support, then follow Open Settings to Repair in the Companion app.',
      );
      return null;
    }

    try {
      const action = COMPANION_COMMAND_ACTIONS[command];
      const res = await companionLocalFetch(
        `/${command}`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: body ? JSON.stringify(body) : undefined,
        },
        action,
      );
      
      if (!res.ok) {
        const errorMessage = await readCompanionLocalError(
          res,
          `Companion App error: ${res.status}`,
        );
        if (res.status === 403) {
          setError(errorMessage);
        } else if (res.status === 401) {
          setError(errorMessage);
        } else if (res.status === 409) {
          setError(errorMessage);
        } else if (res.status === 500) {
          setError(errorMessage || 'Failed to reach Backend API from Companion App.');
        } else {
           setError(errorMessage);
        }
        return null;
      }

      enableCompanionMonitoring();
      
      // Trigger immediate check to update status
      // Small delay to allow companion to process
      setTimeout(() => checkCompanion(), 500);

      return await res.json();
      
    } catch (err: any) {
      if (isCompanionLocalConnectionError(err)) {
        setError(COMPANION_LOCAL_CONNECTION_UNAVAILABLE_MESSAGE);
      } else if (err instanceof Error && err.message) {
        setError(err.message);
      } else {
        setError('Failed to connect to Companion App.');
      }
      console.error(err);
      return null;
    }
  };

  const handleStart = async () => {
    const name = "";
    const response = await sendCommand('start', { name });
    if (response && response.id) {
        router.push(`/recordings/${response.id}`);
        if (onMeetingEnd) onMeetingEnd(); // Refresh list to show new meeting
    }
  };

  const handlePrimaryAction = () => {
    setError(null);

    if (meetingSurfaceState.buttonMode === 'open-support') {
      router.push('/settings?tab=companion');
      return;
    }

    if (meetingSurfaceState.buttonMode === 'start') {
      void handleStart();
    }
  };

  const handleStop = async () => {
    await sendCommand('stop');
    setElapsedTime(0);
    if (onMeetingEnd) {
        // Small delay to allow backend to receive the final chunk
        setTimeout(onMeetingEnd, 1000);
    }
  };
  const handlePause = () => sendCommand('pause');
  const handleResume = () => sendCommand('resume');

  if (variant === "dashboard") {
    return (
      <div className="rounded-[2rem] border border-white/60 bg-white/82 p-6 shadow-xl shadow-orange-950/5 backdrop-blur dark:border-white/10 dark:bg-gray-950/62 dark:shadow-black/20">
        <div className="flex flex-col gap-5">
          <div className="mt-2 flex items-start gap-3">
            <div className="rounded-2xl bg-orange-100 p-2 text-orange-700 dark:bg-orange-500/10 dark:text-orange-300">
              <Mic className="h-5 w-5" />
            </div>
            <div>
              <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-gray-500 dark:text-gray-400">
                Companion status
              </p>
              <h2 className="text-2xl font-semibold text-gray-950 dark:text-white">
                Meet Now
              </h2>
              <p className="mt-1 text-sm text-gray-600 dark:text-gray-300">
                <span className="font-semibold text-gray-900 dark:text-white">
                  {meetingSurfaceState.statusLabel}
                </span>{' '}
                {meetingSurfaceState.message}
              </p>
              {!hasLiveRecording && meetingSurfaceState.nextStepLabel && (
                <p className="mt-2 text-sm text-gray-600 dark:text-gray-300">
                  <span className="font-semibold text-gray-900 dark:text-white">
                    Next step:
                  </span>{' '}
                  {meetingSurfaceState.nextStepLabel}. {meetingSurfaceState.nextStepMessage}
                </p>
              )}
            </div>
          </div>

          {error && (
            <div className="rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-500/20 dark:bg-red-500/10 dark:text-red-300">
              {error}
            </div>
          )}

          {!hasLiveRecording ? (
            <button
              onClick={handlePrimaryAction}
              disabled={meetingSurfaceState.buttonDisabled}
              className="flex items-center justify-center gap-2 rounded-2xl bg-orange-600 px-4 py-3 text-sm font-semibold text-white transition-colors hover:bg-orange-700 disabled:cursor-not-allowed disabled:bg-orange-300 dark:disabled:bg-orange-900/40"
            >
              <Mic className="w-4 h-4" />
              {meetingSurfaceState.buttonLabel}
            </button>
          ) : (
            <div className="space-y-3">
              <div className="flex items-center justify-between gap-4 rounded-[1.5rem] border border-red-100 bg-red-50 px-4 py-4 text-red-700 dark:border-red-500/20 dark:bg-red-500/10 dark:text-red-300">
                <div className="flex items-center gap-3">
                  <div className={`h-2.5 w-2.5 rounded-full bg-red-500 ${companionStatus === 'recording' ? 'animate-pulse' : ''}`} />
                  <span className="text-sm font-semibold uppercase tracking-[0.16em]">
                    {companionStatus === 'recording' ? 'Recording' : 'Paused'}
                  </span>
                </div>
                <span className="font-mono text-3xl font-semibold text-gray-950 dark:text-white">
                  {formatTime(elapsedTime)}
                </span>
              </div>

              <div className="flex flex-wrap gap-3">
                {companionStatus === 'recording' ? (
                  <button
                    onClick={handlePause}
                    className="inline-flex items-center justify-center gap-2 rounded-2xl border border-gray-300 bg-white px-4 py-3 text-sm font-semibold text-gray-700 transition-colors hover:border-orange-300 hover:text-orange-700 dark:border-gray-700 dark:bg-gray-950/60 dark:text-gray-200 dark:hover:border-orange-500/30 dark:hover:text-orange-300"
                    title="Pause Recording"
                  >
                    <Pause className="w-4 h-4" />
                    Pause
                  </button>
                ) : (
                  <button
                    onClick={handleResume}
                    disabled={localHttpsNeedsRepair}
                    className="inline-flex items-center justify-center gap-2 rounded-2xl border border-gray-300 bg-white px-4 py-3 text-sm font-semibold text-gray-700 transition-colors hover:border-orange-300 hover:text-orange-700 dark:border-gray-700 dark:bg-gray-950/60 dark:text-gray-200 dark:hover:border-orange-500/30 dark:hover:text-orange-300"
                    title="Resume Recording"
                  >
                    <Circle className="w-4 h-4 fill-red-500" />
                    Resume
                  </button>
                )}

                <button
                  onClick={handleStop}
                  disabled={localHttpsNeedsRepair}
                  className="inline-flex items-center justify-center gap-2 rounded-2xl bg-red-600 px-4 py-3 text-sm font-semibold text-white transition-colors hover:bg-red-700"
                  title="Stop Recording"
                >
                  <Square className="w-4 h-4 fill-current" />
                  Stop
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="p-4 border-b border-gray-300 dark:border-gray-800 bg-gray-50 dark:bg-gray-950">
      <div className="w-full">
        {error && <div className="text-xs text-red-500 mb-2">{error}</div>}

        {!hasLiveRecording && (
          <div className="mb-3 rounded-lg border border-gray-200 bg-white px-3 py-3 dark:border-gray-800 dark:bg-gray-900/50">
            <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-gray-500 dark:text-gray-400">
              Companion status
            </p>
            <p className="mt-1 text-sm font-semibold text-gray-900 dark:text-white">
              {meetingSurfaceState.statusLabel}
            </p>
            <p className="mt-1 text-xs leading-5 text-gray-600 dark:text-gray-300">
              {meetingSurfaceState.message}
            </p>
            {meetingSurfaceState.nextStepLabel && (
              <p className="mt-2 text-xs leading-5 text-gray-600 dark:text-gray-300">
                <span className="font-semibold text-gray-900 dark:text-white">
                  Next step:
                </span>{' '}
                {meetingSurfaceState.nextStepLabel}. {meetingSurfaceState.nextStepMessage}
              </p>
            )}
          </div>
        )}
        
        {!hasLiveRecording ? (
          <button
            onClick={handlePrimaryAction}
            disabled={meetingSurfaceState.buttonDisabled}
            className="flex items-center justify-center gap-2 w-full bg-orange-600 hover:bg-orange-700 text-white py-2 px-4 rounded-md font-medium transition-colors"
          >
            <Mic className="w-4 h-4" />
            {meetingSurfaceState.buttonLabel}
          </button>
        ) : (
          <div className="flex items-center gap-2 w-full">
            <div className="flex-1 flex items-center gap-2 px-3 py-2 bg-red-50 dark:bg-red-900/20 text-red-600 dark:text-red-400 rounded-lg border border-red-100 dark:border-red-900/30">
              <div className={`w-2 h-2 rounded-full bg-red-500 ${companionStatus === 'recording' ? 'animate-pulse' : ''}`} />
              <span className="text-sm font-medium">
                {companionStatus === 'recording' ? 'Recording' : 'Paused'}
              </span>
              <span className="ml-auto font-mono text-sm">
                {formatTime(elapsedTime)}
              </span>
            </div>
            
            {companionStatus === 'recording' ? (
              <button
                onClick={handlePause}
                disabled={localHttpsNeedsRepair}
                className="p-2 text-gray-600 hover:text-gray-900 dark:text-gray-400 dark:hover:text-gray-100 hover:bg-gray-100 dark:hover:bg-gray-800 rounded-lg transition-colors"
                title="Pause Recording"
              >
                <Pause className="w-5 h-5" />
              </button>
            ) : (
              <button
                onClick={handleResume}
                disabled={localHttpsNeedsRepair}
                className="p-2 text-gray-600 hover:text-gray-900 dark:text-gray-400 dark:hover:text-gray-100 hover:bg-gray-100 dark:hover:bg-gray-800 rounded-lg transition-colors"
                title="Resume Recording"
              >
                <Circle className="w-5 h-5 fill-red-500" />
              </button>
            )}
            
            <button
              onClick={handleStop}
              disabled={localHttpsNeedsRepair}
              className="p-2 text-red-600 hover:text-red-700 hover:bg-red-50 dark:hover:bg-red-900/20 rounded-lg transition-colors"
              title="Stop Recording"
            >
              <Square className="w-5 h-5 fill-current" />
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
