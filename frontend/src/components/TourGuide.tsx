'use client';

import { useEffect, useState } from 'react';
import { usePathname } from 'next/navigation';
import { driver } from 'driver.js';
import 'driver.js/dist/driver.css';
import '@/app/driver-theme.css';
import { useNavigationStore } from '@/lib/store';
import { dashboardSteps, recordingsSteps, transcriptSteps } from '@/lib/tour-config';
import { getUserMe } from '@/lib/api';

export default function TourGuide() {
  const pathname = usePathname();
  const {
    hasSeenTour,
    setHasSeenTour,
    hasSeenRecordingsTour,
    setHasSeenRecordingsTour,
    hasSeenTranscriptTour,
    setHasSeenTranscriptTour,
  } = useNavigationStore();
  const [userId, setUserId] = useState<number | null>(null);

  const getValidSteps = (steps: typeof dashboardSteps) =>
    steps.filter((step) => {
      if (typeof step.element === 'string') {
        return !!document.querySelector(step.element);
      }

      return true;
    });

  const startTour = (
    steps: typeof dashboardSteps,
    onDestroyed: () => void,
  ) => {
    const validSteps = getValidSteps(steps);
    if (validSteps.length === 0) {
      return;
    }

    const driverObj = driver({
      showProgress: true,
      steps: validSteps,
      popoverClass: 'driverjs-theme',
      nextBtnText: 'Next',
      prevBtnText: 'Previous',
      doneBtnText: 'Done',
      onDestroyed,
    });

    driverObj.drive();
  };

  useEffect(() => {
    getUserMe().then(user => setUserId(user.id)).catch(() => {});
  }, []);

  useEffect(() => {
    if (!userId) return;

    // Dashboard Tour
    if (pathname === '/' && !hasSeenTour[userId]) {
      const timer = setTimeout(() => {
        startTour(dashboardSteps, () => {
          setHasSeenTour(userId, true);
        });
      }, 1500);
      return () => clearTimeout(timer);
    }

    // Recordings Tour
    if (pathname === '/recordings' && !hasSeenRecordingsTour[userId]) {
      const timer = setTimeout(() => {
        startTour(recordingsSteps, () => {
          setHasSeenRecordingsTour(userId, true);
        });
      }, 1500);
      return () => clearTimeout(timer);
    }

    // Transcript Tour
    if (pathname?.startsWith('/recordings/') && !hasSeenTranscriptTour[userId]) {
      const timer = setTimeout(() => {
        startTour(transcriptSteps, () => {
          setHasSeenTranscriptTour(userId, true);
        });
      }, 1500);
      return () => clearTimeout(timer);
    }

  }, [
    pathname,
    hasSeenTour,
    setHasSeenTour,
    hasSeenRecordingsTour,
    setHasSeenRecordingsTour,
    hasSeenTranscriptTour,
    setHasSeenTranscriptTour,
    userId,
  ]);

  return null;
}
