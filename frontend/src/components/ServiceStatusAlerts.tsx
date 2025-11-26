"use client";

import { useState, useEffect, useRef } from 'react';

interface HealthStatus {
  status: string;
  version: string;
  components: {
    db: string;
    worker: string;
  };
}

interface AudioLevels {
  input_level: number;
  output_level: number;
  is_recording: boolean;
}

interface ServiceStatus {
  backend: boolean;
  db: boolean;
  worker: boolean;
  companion: boolean;
}

interface AudioWarnings {
  noInput: boolean;
  noOutput: boolean;
}

// Threshold below which we consider the audio "silent" (0-100 scale)
const SILENCE_THRESHOLD = 2;
// Number of consecutive checks before showing warning
const SILENCE_CHECK_COUNT = 3;

export default function ServiceStatusAlerts() {
  const [status, setStatus] = useState<ServiceStatus>({
    backend: true,
    db: true,
    worker: true,
    companion: true,
  });
  
  const [audioWarnings, setAudioWarnings] = useState<AudioWarnings>({
    noInput: false,
    noOutput: false,
  });
  
  // Track consecutive silence counts
  const silenceCountRef = useRef({ input: 0, output: 0 });

  useEffect(() => {
    const checkServices = async () => {
      const newStatus = { ...status };

      // 1. Check Backend & Infrastructure (DB, Worker)
      try {
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 5000);
        
        const res = await fetch('http://localhost:8000/health', { 
          signal: controller.signal,
          method: 'GET'
        });
        clearTimeout(timeoutId);
        
        if (res.ok) {
          const data: HealthStatus = await res.json();
          newStatus.backend = true;
          newStatus.db = data.components.db === 'connected';
          newStatus.worker = data.components.worker === 'active';
        } else {
          // Backend responded but with error code
          newStatus.backend = false;
          // Assume others are unknown/down if backend is erroring
          newStatus.db = false; 
          newStatus.worker = false;
        }
      } catch (error) {
        // Network error / timeout -> Backend unreachable
        newStatus.backend = false;
        // Cannot know status of others
        newStatus.db = false; 
        newStatus.worker = false;
      }

      // 2. Check Companion App
      try {
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 2000);
        
        const res = await fetch('http://localhost:12345/status', { 
          signal: controller.signal,
          method: 'GET'
        });
        clearTimeout(timeoutId);
        
        newStatus.companion = res.ok;
      } catch (error) {
        newStatus.companion = false;
      }

      setStatus(newStatus);
    };
    
    const checkAudioLevels = async () => {
      try {
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 1000);
        
        const res = await fetch('http://localhost:12345/levels', { 
          signal: controller.signal,
          method: 'GET'
        });
        clearTimeout(timeoutId);
        
        if (res.ok) {
          const data: AudioLevels = await res.json();
          
          // Only check levels when recording
          if (data.is_recording) {
            // Check input (microphone)
            if (data.input_level < SILENCE_THRESHOLD) {
              silenceCountRef.current.input++;
            } else {
              silenceCountRef.current.input = 0;
            }
            
            // Check output (system audio)
            if (data.output_level < SILENCE_THRESHOLD) {
              silenceCountRef.current.output++;
            } else {
              silenceCountRef.current.output = 0;
            }
            
            setAudioWarnings({
              noInput: silenceCountRef.current.input >= SILENCE_CHECK_COUNT,
              noOutput: silenceCountRef.current.output >= SILENCE_CHECK_COUNT,
            });
          } else {
            // Reset when not recording
            silenceCountRef.current = { input: 0, output: 0 };
            setAudioWarnings({ noInput: false, noOutput: false });
          }
        }
      } catch (error) {
        // Companion not available, reset warnings
        silenceCountRef.current = { input: 0, output: 0 };
        setAudioWarnings({ noInput: false, noOutput: false });
      }
    };

    // Check immediately
    checkServices();

    // Poll every 5 seconds for service status
    const serviceInterval = setInterval(checkServices, 5000);
    
    // Poll every 2 seconds for audio levels (more responsive)
    const audioInterval = setInterval(checkAudioLevels, 2000);

    return () => {
      clearInterval(serviceInterval);
      clearInterval(audioInterval);
    };
  }, []);

  // Helper to render an alert bubble (error style)
  const renderAlert = (message: string, subMessage?: string) => (
    <div className="mb-2 last:mb-0 w-full max-w-sm bg-red-50 border-l-4 border-red-500 p-4 shadow-lg rounded-r-md animate-pulse">
      <div className="flex items-center">
        <div className="flex-shrink-0">
          <svg className="h-5 w-5 text-red-400" viewBox="0 0 20 20" fill="currentColor">
            <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.707 7.293a1 1 0 00-1.414 1.414L8.586 10l-1.293 1.293a1 1 0 101.414 1.414L10 11.414l1.293 1.293a1 1 0 001.414-1.414L11.414 10l1.293-1.293a1 1 0 00-1.414-1.414L10 8.586 8.707 7.293z" clipRule="evenodd" />
          </svg>
        </div>
        <div className="ml-3">
          <p className="text-sm text-red-700">
            <span className="font-medium">{message}</span>
            {subMessage && (
              <>
                <br />
                {subMessage}
              </>
            )}
          </p>
        </div>
      </div>
    </div>
  );
  
  // Helper to render a warning bubble (amber style for audio warnings)
  const renderWarning = (message: string, subMessage?: string) => (
    <div className="mb-2 last:mb-0 w-full max-w-sm bg-amber-50 border-l-4 border-amber-500 p-4 shadow-lg rounded-r-md">
      <div className="flex items-center">
        <div className="flex-shrink-0">
          <svg className="h-5 w-5 text-amber-400" viewBox="0 0 20 20" fill="currentColor">
            <path fillRule="evenodd" d="M8.257 3.099c.765-1.36 2.722-1.36 3.486 0l5.58 9.92c.75 1.334-.213 2.98-1.742 2.98H4.42c-1.53 0-2.493-1.646-1.743-2.98l5.58-9.92zM11 13a1 1 0 11-2 0 1 1 0 012 0zm-1-8a1 1 0 00-1 1v3a1 1 0 002 0V6a1 1 0 00-1-1z" clipRule="evenodd" />
          </svg>
        </div>
        <div className="ml-3">
          <p className="text-sm text-amber-700">
            <span className="font-medium">{message}</span>
            {subMessage && (
              <>
                <br />
                {subMessage}
              </>
            )}
          </p>
        </div>
      </div>
    </div>
  );

  // Check if there are any alerts to show
  const hasServiceAlerts = !status.backend || !status.db || !status.worker || !status.companion;
  const hasAudioWarnings = audioWarnings.noInput || audioWarnings.noOutput;
  
  if (!hasServiceAlerts && !hasAudioWarnings) {
    return null;
  }

  return (
    <div className="fixed bottom-4 right-4 z-50 flex flex-col items-end">
      {!status.backend && renderAlert("Server Unreachable", "Cannot connect to Nojoin Backend API.")}
      
      {/* Only show DB/Worker errors if backend is UP, otherwise it's redundant/unknown */}
      {status.backend && !status.db && renderAlert("Database Error", "Connection to PostgreSQL failed.")}
      {status.backend && !status.worker && renderAlert("Worker Offline", "Background processing is paused.")}
      
      {!status.companion && renderAlert("Companion App Disconnected", "Start the app to record audio.")}
      
      {/* Audio warnings (only show when companion is connected and recording) */}
      {status.companion && audioWarnings.noInput && renderWarning(
        "No Microphone Audio Detected", 
        "Check if your mic is muted or disconnected."
      )}
      {status.companion && audioWarnings.noOutput && renderWarning(
        "No System Audio Detected", 
        "Check your volume settings or audio output device."
      )}
    </div>
  );
}
