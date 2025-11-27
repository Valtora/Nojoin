"use client";

import { useState, useEffect, useRef } from 'react';
import { useNotificationStore } from '@/lib/notificationStore';

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
  noAudio: boolean;
}

// Threshold below which we consider the audio "silent" (0-100 scale)
const SILENCE_THRESHOLD = 2;
// Number of consecutive checks before showing warning
const SILENCE_CHECK_COUNT = 3;

export default function ServiceStatusAlerts() {
  const { addNotification, removeActiveNotification } = useNotificationStore();
  
  const [status, setStatus] = useState<ServiceStatus>({
    backend: true,
    db: true,
    worker: true,
    companion: true,
  });
  
  const [audioWarnings, setAudioWarnings] = useState<AudioWarnings>({
    noAudio: false,
  });
  
  // Track consecutive silence counts
  const silenceCountRef = useRef({ input: 0, output: 0 });
  
  // Track active notification IDs
  const notificationIds = useRef<{ [key: string]: string | null }>({
    backend: null,
    db: null,
    worker: null,
    companion: null,
    audio: null
  });

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
          newStatus.backend = false;
          newStatus.db = false; 
          newStatus.worker = false;
        }
      } catch (error) {
        newStatus.backend = false;
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
      updateNotifications(newStatus);
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
          
          if (data.is_recording) {
            if (data.input_level < SILENCE_THRESHOLD) {
              silenceCountRef.current.input++;
            } else {
              silenceCountRef.current.input = 0;
            }
            
            if (data.output_level < SILENCE_THRESHOLD) {
              silenceCountRef.current.output++;
            } else {
              silenceCountRef.current.output = 0;
            }
            
            const isInputSilent = silenceCountRef.current.input >= SILENCE_CHECK_COUNT;
            const isOutputSilent = silenceCountRef.current.output >= SILENCE_CHECK_COUNT;

            const newWarnings = { noAudio: isInputSilent && isOutputSilent };
            setAudioWarnings(newWarnings);
            updateAudioNotifications(newWarnings);
          } else {
            silenceCountRef.current = { input: 0, output: 0 };
            setAudioWarnings({ noAudio: false });
            updateAudioNotifications({ noAudio: false });
          }
        }
      } catch (error) {
        silenceCountRef.current = { input: 0, output: 0 };
        setAudioWarnings({ noAudio: false });
        updateAudioNotifications({ noAudio: false });
      }
    };

    const updateNotifications = (currentStatus: ServiceStatus) => {
      // Backend
      if (!currentStatus.backend && !notificationIds.current.backend) {
        notificationIds.current.backend = addNotification({
          type: 'error',
          message: 'Server Unreachable: Cannot connect to Nojoin Backend API.',
          persistent: true
        });
      } else if (currentStatus.backend && notificationIds.current.backend) {
        removeActiveNotification(notificationIds.current.backend);
        notificationIds.current.backend = null;
      }

      // DB (only if backend is up)
      if (currentStatus.backend && !currentStatus.db && !notificationIds.current.db) {
        notificationIds.current.db = addNotification({
          type: 'error',
          message: 'Database Error: Connection to PostgreSQL failed.',
          persistent: true
        });
      } else if ((!currentStatus.backend || currentStatus.db) && notificationIds.current.db) {
        removeActiveNotification(notificationIds.current.db);
        notificationIds.current.db = null;
      }

      // Worker (only if backend is up)
      if (currentStatus.backend && !currentStatus.worker && !notificationIds.current.worker) {
        notificationIds.current.worker = addNotification({
          type: 'error',
          message: 'Worker Offline: Background processing is paused.',
          persistent: true
        });
      } else if ((!currentStatus.backend || currentStatus.worker) && notificationIds.current.worker) {
        removeActiveNotification(notificationIds.current.worker);
        notificationIds.current.worker = null;
      }

      // Companion
      if (!currentStatus.companion && !notificationIds.current.companion) {
        notificationIds.current.companion = addNotification({
          type: 'error',
          message: 'Companion App Disconnected: Start the app to record audio.',
          persistent: true
        });
      } else if (currentStatus.companion && notificationIds.current.companion) {
        removeActiveNotification(notificationIds.current.companion);
        notificationIds.current.companion = null;
      }
    };

    const updateAudioNotifications = (warnings: AudioWarnings) => {
      if (warnings.noAudio && !notificationIds.current.audio) {
        notificationIds.current.audio = addNotification({
          type: 'warning',
          message: 'No Audio Detected: No sound detected from microphone or system.',
          persistent: true
        });
      } else if (!warnings.noAudio && notificationIds.current.audio) {
        removeActiveNotification(notificationIds.current.audio);
        notificationIds.current.audio = null;
      }
    };

    checkServices();
    const serviceInterval = setInterval(checkServices, 5000);
    const audioInterval = setInterval(checkAudioLevels, 2000);

    return () => {
      clearInterval(serviceInterval);
      clearInterval(audioInterval);
    };
  }, [addNotification, removeActiveNotification, status]);

  return null;
}
