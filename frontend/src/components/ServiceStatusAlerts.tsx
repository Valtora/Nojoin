"use client";

import { useState, useEffect, useRef } from 'react';
import { useNotificationStore } from '@/lib/notificationStore';
import { useServiceStatusStore } from '@/lib/serviceStatusStore';

// Threshold below which we consider the audio "silent" (0-100 scale)
const SILENCE_THRESHOLD = 2;
// Number of consecutive checks before showing warning
const SILENCE_CHECK_COUNT = 3;

export default function ServiceStatusAlerts() {
  const { addNotification, removeActiveNotification } = useNotificationStore();
  const { 
    backend, db, worker, companion, 
    audioLevels, companionStatus,
    startPolling, stopPolling 
  } = useServiceStatusStore();
  
  const [audioWarnings, setAudioWarnings] = useState<{ noAudio: boolean }>({
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

  // Track startup grace period
  const isStartupRef = useRef(true);

  useEffect(() => {
    const timer = setTimeout(() => {
      isStartupRef.current = false;
    }, 10000); // 10 seconds grace period
    return () => clearTimeout(timer);
  }, []);

  // Start polling on mount
  useEffect(() => {
    startPolling();
    return () => stopPolling();
  }, [startPolling, stopPolling]);

  // Monitor Service Status
  useEffect(() => {
    const updateNotifications = () => {
      // Backend
      if (!backend && !notificationIds.current.backend) {
        // Only show error if not in startup grace period
        if (!isStartupRef.current) {
          notificationIds.current.backend = addNotification({
            type: 'error',
            message: 'Server Unreachable: Cannot connect to Nojoin Backend API.',
            persistent: true
          });
        }
      } else if (backend && notificationIds.current.backend) {
        removeActiveNotification(notificationIds.current.backend);
        notificationIds.current.backend = null;
      }

      // DB (only if backend is up)
      if (backend && !db && !notificationIds.current.db) {
        notificationIds.current.db = addNotification({
          type: 'error',
          message: 'Database Error: Connection to PostgreSQL failed.',
          persistent: true
        });
      } else if ((!backend || db) && notificationIds.current.db) {
        removeActiveNotification(notificationIds.current.db);
        notificationIds.current.db = null;
      }

      // Worker (only if backend is up)
      if (backend && !worker && !notificationIds.current.worker) {
        notificationIds.current.worker = addNotification({
          type: 'error',
          message: 'Worker Offline: Background processing is paused.',
          persistent: true
        });
      } else if ((!backend || worker) && notificationIds.current.worker) {
        removeActiveNotification(notificationIds.current.worker);
        notificationIds.current.worker = null;
      }

      // Companion
      if (!companion && !notificationIds.current.companion) {
        notificationIds.current.companion = addNotification({
          type: 'error',
          message: 'Companion App Disconnected: Start the app to record audio.',
          persistent: true
        });
      } else if (companion && notificationIds.current.companion) {
        removeActiveNotification(notificationIds.current.companion);
        notificationIds.current.companion = null;
      }
    };

    updateNotifications();
  }, [backend, db, worker, companion, addNotification, removeActiveNotification]);

  // Monitor Audio Levels
  useEffect(() => {
    const checkAudioSilence = () => {
      if (companionStatus === 'recording') {
        if (audioLevels.input < SILENCE_THRESHOLD) {
          silenceCountRef.current.input++;
        } else {
          silenceCountRef.current.input = 0;
        }
        
        if (audioLevels.output < SILENCE_THRESHOLD) {
          silenceCountRef.current.output++;
        } else {
          silenceCountRef.current.output = 0;
        }
        
        const isInputSilent = silenceCountRef.current.input >= SILENCE_CHECK_COUNT;
        const isOutputSilent = silenceCountRef.current.output >= SILENCE_CHECK_COUNT;

        const newWarnings = { noAudio: isInputSilent && isOutputSilent };
        setAudioWarnings(newWarnings);
        
        if (newWarnings.noAudio && !notificationIds.current.audio) {
          notificationIds.current.audio = addNotification({
            type: 'warning',
            message: 'No Audio Detected: No sound detected from microphone or system.',
            persistent: true
          });
        } else if (!newWarnings.noAudio && notificationIds.current.audio) {
          removeActiveNotification(notificationIds.current.audio);
          notificationIds.current.audio = null;
        }
      } else {
        silenceCountRef.current = { input: 0, output: 0 };
        setAudioWarnings({ noAudio: false });
        if (notificationIds.current.audio) {
          removeActiveNotification(notificationIds.current.audio);
          notificationIds.current.audio = null;
        }
      }
    };

    checkAudioSilence();
  }, [audioLevels, companionStatus, addNotification, removeActiveNotification]);

  return null;
}
