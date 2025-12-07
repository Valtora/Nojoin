'use client';

import { useEffect } from 'react';
import { usePathname } from 'next/navigation';
import { driver } from 'driver.js';
import 'driver.js/dist/driver.css';
import '@/app/driver-theme.css';
import { useNavigationStore } from '@/lib/store';
import { dashboardSteps, transcriptSteps } from '@/lib/tour-config';

export default function TourGuide() {
  const pathname = usePathname();
  const { hasSeenTour, setHasSeenTour, hasSeenTranscriptTour, setHasSeenTranscriptTour } = useNavigationStore();

  useEffect(() => {
    // Dashboard Tour
    if (pathname === '/' && !hasSeenTour) {
      const timer = setTimeout(() => {
        const driverObj = driver({
          showProgress: true,
          steps: dashboardSteps,
          popoverClass: 'driverjs-theme',
          nextBtnText: 'Next',
          prevBtnText: 'Previous',
          doneBtnText: 'Done',
          onDestroyed: () => {
            setHasSeenTour(true);
          },
        });

        driverObj.drive();
      }, 1500);
      return () => clearTimeout(timer);
    }

    // Transcript Tour
    if (pathname?.startsWith('/recordings/') && !hasSeenTranscriptTour) {
       const timer = setTimeout(() => {
        const driverObj = driver({
          showProgress: true,
          steps: transcriptSteps,
          popoverClass: 'driverjs-theme',
          nextBtnText: 'Next',
          prevBtnText: 'Previous',
          doneBtnText: 'Done',
          onDestroyed: () => {
            setHasSeenTranscriptTour(true);
          },
        });

        driverObj.drive();
      }, 1500);
      return () => clearTimeout(timer);
    }

  }, [pathname, hasSeenTour, setHasSeenTour, hasSeenTranscriptTour, setHasSeenTranscriptTour]);

  return null;
}
