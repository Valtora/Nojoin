'use client';

import { useCallback, useEffect, useState } from 'react';
import { usePathname } from 'next/navigation';
import { driver, type Driver } from 'driver.js';
import 'driver.js/dist/driver.css';
import '@/app/driver-theme.css';
import { useNavigationStore } from '@/lib/store';
import { dashboardSteps, recordingsSteps, transcriptSteps } from '@/lib/tour-config';
import { getUserMe } from '@/lib/api';

function useTourUserId(enabled = true) {
  const [userId, setUserId] = useState<number | null>(null);
  useEffect(() => {
    if (!enabled || userId !== null) return;
    let cancelled = false;
    getUserMe().then(user => {
      if (!cancelled) setUserId(user.id);
    }).catch(() => {});
    return () => { cancelled = true; };
  }, [enabled, userId]);
  return userId;
}

function useScheduledTour(
  steps: typeof dashboardSteps,
  enabled: boolean,
  scope: string,
  onSeen: () => void,
) {
  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    let activeDriver: Driver | null = null;
    const timer = setTimeout(() => {
      const validSteps = steps.filter(step =>
        typeof step.element !== 'string' || document.querySelector(step.element),
      );
      if (validSteps.length === 0) return;
      activeDriver = driver({
        showProgress: true,
        steps: validSteps,
        popoverClass: 'driverjs-theme',
        nextBtnText: 'Next',
        prevBtnText: 'Previous',
        doneBtnText: 'Done',
        onDestroyed: () => {
          // Route/state cleanup must leave the tour available for a later visit.
          if (!cancelled) onSeen();
        },
      });
      activeDriver.drive();
    }, 1500);
    return () => {
      cancelled = true;
      clearTimeout(timer);
      activeDriver?.destroy();
    };
  }, [enabled, onSeen, scope, steps]);
}

/** Detail metadata owns eligibility, including completion without navigation. */
export function RecordingTranscriptTour({ recordingId, eligible }: {
  recordingId: string;
  eligible: boolean;
}) {
  const userId = useTourUserId(eligible);
  const { hasSeenTranscriptTour, setHasSeenTranscriptTour } = useNavigationStore();
  const onSeen = useCallback(() => {
    if (userId !== null) setHasSeenTranscriptTour(userId, true);
  }, [setHasSeenTranscriptTour, userId]);
  useScheduledTour(transcriptSteps,
    eligible && userId !== null && !hasSeenTranscriptTour[userId], recordingId, onSeen);
  return null;
}

export default function TourGuide() {
  const pathname = usePathname();
  const userId = useTourUserId();
  const { hasSeenTour, setHasSeenTour, hasSeenRecordingsTour, setHasSeenRecordingsTour } = useNavigationStore();
  const isDashboard = pathname === '/';
  const isRecordings = pathname === '/recordings';
  const seen = userId === null || (isDashboard ? hasSeenTour[userId] : hasSeenRecordingsTour[userId]);
  const onSeen = useCallback(() => {
    if (userId === null) return;
    if (isDashboard) setHasSeenTour(userId, true);
    else setHasSeenRecordingsTour(userId, true);
  }, [isDashboard, setHasSeenRecordingsTour, setHasSeenTour, userId]);
  useScheduledTour(isDashboard ? dashboardSteps : recordingsSteps,
    (isDashboard || isRecordings) && !seen, pathname ?? '', onSeen);
  return null;
}
