"use client";

import { useState, useEffect } from 'react';
import { Play, Square, Pause, Mic } from 'lucide-react';
import { useRouter } from 'next/navigation';

type RecordingStatus = 'idle' | 'recording' | 'paused';

interface MeetingControlsProps {
  onMeetingEnd?: () => void;
}

export default function MeetingControls({ onMeetingEnd }: MeetingControlsProps) {
  const [status, setStatus] = useState<RecordingStatus>('idle');
  const [error, setError] = useState<string | null>(null);
  const router = useRouter();

  // Poll status on mount to sync with Companion App
  useEffect(() => {
    const fetchStatus = async () => {
      try {
        const res = await fetch('http://localhost:12345/status');
        if (res.ok) {
          const data = await res.json();
          
          if (typeof data === 'string') {
            const s = data.toLowerCase();
            if (s === 'idle') setStatus('idle');
            else if (s === 'recording') setStatus('recording');
            else if (s === 'paused') setStatus('paused');
          } else if (typeof data === 'object' && data.Error) {
             // Handle error state if needed
             setStatus('idle'); // Fallback
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
      if (command === 'start') setStatus('recording');
      if (command === 'stop') setStatus('idle');
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
    const name = `Meeting ${new Date().toLocaleString()}`;
    const response = await sendCommand('start', { name });
    if (response && response.id) {
        router.push(`/recordings/${response.id}`);
        if (onMeetingEnd) onMeetingEnd(); // Refresh list to show new meeting
    }
  };

  const handleStop = async () => {
    await sendCommand('stop');
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
          <div className="flex gap-2">
            <button
              onClick={handleStop}
              className="flex-1 flex items-center justify-center gap-2 bg-red-600 hover:bg-red-700 text-white py-2 px-4 rounded-md font-medium transition-colors"
            >
              <Square className="w-4 h-4 fill-current" />
              End
            </button>
            
            {status === 'recording' ? (
              <button
                onClick={handlePause}
                className="flex-1 flex items-center justify-center gap-2 bg-gray-200 dark:bg-gray-800 hover:bg-gray-300 dark:hover:bg-gray-700 text-gray-900 dark:text-gray-100 py-2 px-4 rounded-md font-medium transition-colors"
              >
                <Pause className="w-4 h-4 fill-current" />
                Pause
              </button>
            ) : (
              <button
                onClick={handleResume}
                className="flex-1 flex items-center justify-center gap-2 bg-green-600 hover:bg-green-700 text-white py-2 px-4 rounded-md font-medium transition-colors"
              >
                <Play className="w-4 h-4 fill-current" />
                Resume
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
