"use client";

import { useState, useEffect, useRef } from 'react';
import { Play, Square, Pause, Mic } from 'lucide-react';
import { useRouter } from 'next/navigation';

type RecordingStatus = 'idle' | 'recording' | 'paused';

interface MeetingControlsProps {
  onMeetingEnd?: () => void;
}

export default function MeetingControls({ onMeetingEnd }: MeetingControlsProps) {
  const [status, setStatus] = useState<RecordingStatus>('idle');
  const [error, setError] = useState<string | null>(null);
  const [elapsedTime, setElapsedTime] = useState<number>(0);
  const timerRef = useRef<NodeJS.Timeout | null>(null);
  const router = useRouter();

  // Poll status on mount to sync with Companion App
  useEffect(() => {
    const fetchStatus = async () => {
      try {
        const res = await fetch('http://localhost:12345/status');
        if (res.ok) {
          const data = await res.json();
          
          // Handle new object response { status: string, duration_seconds: number }
          if (typeof data === 'object' && data.status) {
             // Handle nested status object if it's an enum variant like { "Error": "msg" }
             let s = '';
             if (typeof data.status === 'string') {
                 s = data.status.toLowerCase();
             } else if (typeof data.status === 'object') {
                 // e.g. { "Error": "..." }
                 s = Object.keys(data.status)[0].toLowerCase();
             }

             if (s === 'idle') {
                 setStatus('idle');
                 setElapsedTime(0);
             } else if (s === 'recording') {
                 setStatus('recording');
                 if (typeof data.duration_seconds === 'number') {
                     setElapsedTime(data.duration_seconds);
                 }
             } else if (s === 'paused') {
                 setStatus('paused');
                 if (typeof data.duration_seconds === 'number') {
                     setElapsedTime(data.duration_seconds);
                 }
             }
          }
          // Fallback for old string response (just in case)
          else if (typeof data === 'string') {
            const s = data.toLowerCase();
            if (s === 'idle') {
                setStatus('idle');
                setElapsedTime(0);
            }
            else if (s === 'recording') setStatus('recording');
            else if (s === 'paused') setStatus('paused');
          }
        }
      } catch (e) {
        // Companion might be down
      }
    };
    
    fetchStatus();
    const interval = setInterval(fetchStatus, 2000);
    return () => clearInterval(interval);
  }, []);

  // Timer logic
  useEffect(() => {
    if (status === 'recording') {
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
  }, [status]);

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
      const res = await fetch(`http://localhost:12345/${command}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: body ? JSON.stringify(body) : undefined,
      });
      
      if (!res.ok) {
        throw new Error(`Failed to ${command}`);
      }
      
      // Optimistic update
      if (command === 'start') {
          setStatus('recording');
          setElapsedTime(0);
      }
      if (command === 'stop') {
          setStatus('idle');
          setElapsedTime(0);
      }
      if (command === 'pause') setStatus('paused');
      if (command === 'resume') setStatus('recording');

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
    <div className="p-4 border-b border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-900">
      <div className="flex flex-col gap-2">
        {error && <div className="text-xs text-red-500">{error}</div>}
        
        {status === 'idle' ? (
          <button
            onClick={handleStart}
            className="flex items-center justify-center gap-2 w-full bg-orange-600 hover:bg-orange-700 text-white py-2 px-4 rounded-md font-medium transition-colors"
          >
            <Mic className="w-4 h-4" />
            Start Meeting
          </button>
        ) : (
          <div className="flex items-center gap-2">
            <div className="flex-1 flex items-center gap-2 px-3 py-2 bg-red-50 dark:bg-red-900/20 text-red-600 dark:text-red-400 rounded-lg border border-red-100 dark:border-red-900/30">
              <div className={`w-2 h-2 rounded-full bg-red-500 ${status === 'recording' ? 'animate-pulse' : ''}`} />
              <span className="text-sm font-medium">
                {status === 'recording' ? 'Recording' : 'Paused'}
              </span>
              <span className="ml-auto font-mono text-sm">
                {formatTime(elapsedTime)}
              </span>
            </div>
            
            {status === 'recording' ? (
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
                <Play className="w-5 h-5" />
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
