"use client";

import { useState, useEffect, useRef } from 'react';
import { Square, Pause, Mic, Circle } from 'lucide-react';
import { useRouter } from 'next/navigation';
import { useServiceStatusStore } from '@/lib/serviceStatusStore';
import { getCompanionToken } from '@/lib/api';

interface MeetingControlsProps {
  onMeetingEnd?: () => void;
  variant?: "sidebar" | "dashboard";
}

export default function MeetingControls({
  onMeetingEnd,
  variant = "sidebar",
}: MeetingControlsProps) {
  const {
    companion,
    companionAuthenticated,
    companionStatus,
    recordingDuration,
    checkCompanion,
  } = useServiceStatusStore();
  
  // Local state for smooth timer, synced with store
  const [elapsedTime, setElapsedTime] = useState<number>(0);
  const timerRef = useRef<NodeJS.Timeout | null>(null);
  const [error, setError] = useState<string | null>(null);
  const router = useRouter();

  // Sync local timer with store duration
  useEffect(() => {
    if (recordingDuration > 0) {
      setElapsedTime(recordingDuration);
    }
  }, [recordingDuration]);

  // Timer logic
  useEffect(() => {
    if (companionStatus === 'recording') {
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
  }, [companionStatus]);

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
    try {
      const res = await fetch(`http://127.0.0.1:12345/${command}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: body ? JSON.stringify(body) : undefined,
      });
      
      if (!res.ok) {
        if (res.status === 500) {
           setError('Failed to reach Backend API from Companion App.');
        } else {
           setError(`Companion App error: ${res.statusText}`);
        }
        return null;
      }
      
      // Trigger immediate check to update status
      // Small delay to allow companion to process
      setTimeout(() => checkCompanion(), 500);

      return await res.json();
      
    } catch (err: any) {
      if (err instanceof TypeError && err.message === "Failed to fetch") {
        setError('Companion App is offline or unreachable.');
      } else {
        setError('Failed to connect to Companion App.');
      }
      console.error(err);
      return null;
    }
  };

  const handleStart = async () => {
    const name = "";
    const token = await getCompanionToken();
    const response = await sendCommand('start', { name, token });
    if (response && response.id) {
        router.push(`/recordings/${response.id}`);
        if (onMeetingEnd) onMeetingEnd(); // Refresh list to show new meeting
    }
  };

  const handleStop = async () => {
    const token = await getCompanionToken();
    await sendCommand('stop', { token });
    setElapsedTime(0);
    if (onMeetingEnd) {
        // Small delay to allow backend to receive the final chunk
        setTimeout(onMeetingEnd, 1000);
    }
  };
  const handlePause = () => sendCommand('pause');
  const handleResume = () => sendCommand('resume');

  if (variant === "dashboard") {
    const startDisabled = !companion || !companionAuthenticated;
    const heading =
      companionStatus === "recording"
        ? "Recording in progress"
        : companionStatus === "paused"
          ? "Recording paused"
          : "Start a meeting";
    const helperText = !companion
      ? "The companion app is offline. Start it locally to enable live capture."
      : !companionAuthenticated
        ? "Authorise the companion from the main navigation before starting capture."
        : companionStatus === "recording"
          ? "Capture is live. You can pause or stop directly from here."
          : companionStatus === "paused"
            ? "The current meeting is paused and ready to resume."
            : "Launch a new capture session and jump straight into the live meeting workspace.";

    return (
      <div className="rounded-[1.75rem] border border-white/60 bg-white/72 p-5 shadow-lg shadow-orange-950/5 dark:border-white/10 dark:bg-gray-900/60">
        <div className="flex flex-col gap-5">
          <div className="space-y-3">
            <span className="inline-flex items-center gap-2 rounded-full border border-orange-200 bg-orange-50 px-3 py-1 text-xs font-semibold uppercase tracking-[0.2em] text-orange-700 dark:border-orange-500/20 dark:bg-orange-500/10 dark:text-orange-300">
              <Mic className="h-3.5 w-3.5" />
              Quick Capture
            </span>

            <div className="space-y-2">
              <h3 className="text-2xl font-semibold text-gray-950 dark:text-white">
                {heading}
              </h3>
              <p className="text-sm leading-6 text-gray-600 dark:text-gray-300">
                {helperText}
              </p>
            </div>
          </div>

          {error && (
            <div className="rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-500/20 dark:bg-red-500/10 dark:text-red-300">
              {error}
            </div>
          )}

          {companionStatus === 'idle' ? (
            <button
              onClick={handleStart}
              disabled={startDisabled}
              className="flex items-center justify-center gap-2 rounded-2xl bg-orange-600 px-4 py-3 text-sm font-semibold text-white transition-colors hover:bg-orange-700 disabled:cursor-not-allowed disabled:bg-orange-300 dark:disabled:bg-orange-900/40"
            >
              <Mic className="w-4 h-4" />
              Start Meeting
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
                    className="inline-flex items-center justify-center gap-2 rounded-2xl border border-gray-300 bg-white px-4 py-3 text-sm font-semibold text-gray-700 transition-colors hover:border-orange-300 hover:text-orange-700 dark:border-gray-700 dark:bg-gray-950/60 dark:text-gray-200 dark:hover:border-orange-500/30 dark:hover:text-orange-300"
                    title="Resume Recording"
                  >
                    <Circle className="w-4 h-4 fill-red-500" />
                    Resume
                  </button>
                )}

                <button
                  onClick={handleStop}
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
        
        {companionStatus === 'idle' ? (
          <button
            onClick={handleStart}
            className="flex items-center justify-center gap-2 w-full bg-orange-600 hover:bg-orange-700 text-white py-2 px-4 rounded-md font-medium transition-colors"
          >
            <Mic className="w-4 h-4" />
            Start Meeting
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
                className="p-2 text-gray-600 hover:text-gray-900 dark:text-gray-400 dark:hover:text-gray-100 hover:bg-gray-100 dark:hover:bg-gray-800 rounded-lg transition-colors"
                title="Pause Recording"
              >
                <Pause className="w-5 h-5" />
              </button>
            ) : (
              <button
                onClick={handleResume}
                className="p-2 text-gray-600 hover:text-gray-900 dark:text-gray-400 dark:hover:text-gray-100 hover:bg-gray-100 dark:hover:bg-gray-800 rounded-lg transition-colors"
                title="Resume Recording"
              >
                <Circle className="w-5 h-5 fill-red-500" />
              </button>
            )}
            
            <button
              onClick={handleStop}
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
