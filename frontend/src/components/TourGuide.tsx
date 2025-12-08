'use client';

import { useEffect, useState } from 'react';
import { usePathname } from 'next/navigation';
import { driver } from 'driver.js';
import 'driver.js/dist/driver.css';
import '@/app/driver-theme.css';
import { useNavigationStore } from '@/lib/store';
import { dashboardSteps, transcriptSteps } from '@/lib/tour-config';
import { getUserMe } from '@/lib/api';

export default function TourGuide() {
  const pathname = usePathname();
  const { hasSeenTour, setHasSeenTour, hasSeenTranscriptTour, setHasSeenTranscriptTour } = useNavigationStore();
  const [userId, setUserId] = useState<number | null>(null);

  useEffect(() => {
    getUserMe().then(user => setUserId(user.id)).catch(() => {});
  }, []);

  useEffect(() => {
    if (!userId) return;

    // Dashboard Tour
    if (pathname === '/' && !hasSeenTour[userId]) {
      const timer = setTimeout(() => {
        const driverObj = driver({
          showProgress: true,
          steps: dashboardSteps,
          popoverClass: 'driverjs-theme',
          nextBtnText: 'Next',
          prevBtnText: 'Previous',
          doneBtnText: 'Done',
          onDestroyed: () => {
            setHasSeenTour(userId, true);
          },
        });

        driverObj.drive();
      }, 1500);
      return () => clearTimeout(timer);
    }

    // Transcript Tour
    if (pathname?.startsWith('/recordings/') && !hasSeenTranscriptTour[userId]) {
       const timer = setTimeout(() => {
        const driverObj = driver({
          showProgress: true,
          steps: transcriptSteps,
          popoverClass: 'driverjs-theme',
          nextBtnText: 'Next',
          prevBtnText: 'Previous',
          doneBtnText: 'Done',
          onDestroyed: () => {
            setHasSeenTranscriptTour(userId, true);
          },
        });

        driverObj.drive();
      }, 1500);
      return () => clearTimeout(timer);
    }

  }, [pathname, hasSeenTour, setHasSeenTour, hasSeenTranscriptTour, setHasSeenTranscriptTour, userId]);

  return null;
}
