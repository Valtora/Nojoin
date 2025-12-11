"use client";

import { useState, useEffect, useRef } from 'react';
import { Square, Pause, Mic, Circle } from 'lucide-react';
import { useRouter } from 'next/navigation';
import { useServiceStatusStore } from '@/lib/serviceStatusStore';

interface MeetingControlsProps {
  onMeetingEnd?: () => void;
}

export default function MeetingControls({ onMeetingEnd }: MeetingControlsProps) {
  const { companionStatus, recordingDuration, checkCompanion } = useServiceStatusStore();
  
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
        throw new Error(`Failed to ${command}`);
      }
      
      // Trigger immediate check to update status
      // Small delay to allow companion to process
      setTimeout(() => checkCompanion(), 500);

      return await res.json();
      
    } catch (err) {
      setError('Failed to connect to Companion App');
      console.error(err);
      return null;
    }
  };

  const handleStart = async () => {
    const name = "";
    const token = localStorage.getItem('token');
    const response = await sendCommand('start', { name, token });
    if (response && response.id) {
        router.push(`/recordings/${response.id}`);
        if (onMeetingEnd) onMeetingEnd(); // Refresh list to show new meeting
    }
  };

  const handleStop = async () => {
    const token = localStorage.getItem('token');
    await sendCommand('stop', { token });
    setElapsedTime(0);
    if (onMeetingEnd) {
        // Small delay to allow backend to receive the final chunk
        setTimeout(onMeetingEnd, 1000);
    }
  };
  const handlePause = () => sendCommand('pause');
  const handleResume = () => sendCommand('resume');

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
