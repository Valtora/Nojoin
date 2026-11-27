'use client';

import { useCallback, useEffect, useState } from 'react';
import { usePathname } from 'next/navigation';
import { driver, type Driver } from 'driver.js';
import 'driver.js/dist/driver.css';
import '@/app/driver-theme.css';
import { useNavigationStore } from '@/lib/store';
import { dashboardSteps, recordingsSteps, restoreTourPanel, transcriptSteps } from '@/lib/tour-config';
import { getUserMe } from '@/lib/api';
import { whenNotTyping } from '@/lib/whenNotTyping';

// A tour opens this long after its page does.
const TOUR_DELAY_MS = 1500;
// A tour held back while someone types opens once focus has left the field
// and nothing has been pressed or clicked for this long. It is longer than the
// pause people take to choose their next action (about 1.35 s in the
// keystroke-level model), so a tour does not land on the click or key that
// follows leaving a field, such as a click back into it.
const TOUR_IDLE_MS = 1500;
// For this long after a tour opens, a click on its overlay is taken to be one
// that was already on its way to the page, and is ignored (#984). The overlay
// takes 400 ms to fade in, and stopping a click already started takes about
// another quarter of a second (the stop-signal reaction time), so a click
// before about 0.65 s cannot be called off. Deciding to dismiss the tour takes
// longer than a second (1.35 s of mental preparation in the keystroke-level
// model), so a click that means to close it still does.
const TOUR_CLICK_GRACE_MS = 1000;

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
  // Runs however the tour ends, cancelled included.
  onEnd?: () => void,
) {
  useEffect(() => {
    if (!enabled) return;
    let activeDriver: Driver | null = null;
    // driver.js closes a tour when Escape is released, but notes presses only
    // once the tour is open. An Escape pressed before then, to close a menu
    // say, would close the tour on its release and mark it seen, so that
    // release is ignored (#988). These listen in the capture phase on window,
    // so each release is judged before driver.js's own listener acts on it.
    let escapePressedEarly = false;
    let ignoreEscape = false;
    const onEscape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      if (event.type === 'keyup') {
        ignoreEscape = escapePressedEarly;
        escapePressedEarly = false;
      } else if (!event.repeat || !activeDriver) {
        // A repeat before the tour opens is a key held since before these
        // listeners were added, so it counts as early too.
        escapePressedEarly = !activeDriver;
      }
    };
    window.addEventListener('keydown', onEscape, true);
    window.addEventListener('keyup', onEscape, true);
    // Ends the tour once. Only a person closing it marks it seen: a route or
    // state change leaves it for a later visit.
    const end = (seen: boolean) => {
      const ending = activeDriver;
      if (!ending) return;
      activeDriver = null;
      ending.destroy();
      onEnd?.();
      if (seen) onSeen();
    };
    // driver.js takes focus, so a tour waits while someone types (#918). Its
    // steps are read when it opens, since the page may have changed by then.
    const cancelOpen = whenNotTyping(() => {
      const validSteps = steps.filter(step =>
        typeof step.element !== 'string' || document.querySelector(step.element),
      );
      if (validSteps.length === 0) return;
      const openedAt = Date.now();
      activeDriver = driver({
        showProgress: true,
        steps: validSteps,
        popoverClass: 'driverjs-theme',
        nextBtnText: 'Next',
        prevBtnText: 'Previous',
        doneBtnText: 'Done',
        // A click on the overlay comes here instead of closing the tour. Once
        // the grace has passed it closes the tour as anything else does.
        overlayClickBehavior: () => {
          if (Date.now() - openedAt >= TOUR_CLICK_GRACE_MS) end(true);
        },
        // Escape, the close button and Done arrive here, at any time, and
        // all but an early Escape's release end the tour. driver.js skips
        // onDestroyed when a tour closes in the 400 ms a step takes to
        // animate in, so the end can't wait for it (#915).
        onDestroyStarted: (_element, _step, { reason }) => {
          if (reason !== 'escape' || !ignoreEscape) end(true);
        },
      });
      activeDriver.drive();
    }, TOUR_DELAY_MS, TOUR_IDLE_MS);
    return () => {
      cancelOpen();
      window.removeEventListener('keydown', onEscape, true);
      window.removeEventListener('keyup', onEscape, true);
      end(false);
    };
  }, [enabled, onEnd, onSeen, scope, steps]);
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
  // Its steps open the panels they describe; the page puts back the one it found.
  useScheduledTour(transcriptSteps,
    eligible && userId !== null && !hasSeenTranscriptTour[userId], recordingId, onSeen, restoreTourPanel);
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
