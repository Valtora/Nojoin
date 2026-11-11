import { act, fireEvent, render } from '@testing-library/react';
import type { ReactElement } from 'react';
import { afterEach, beforeEach, describe, expect, it, type Mock, vi } from 'vitest';

import { getUserMe } from '@/lib/api';
import { useNavigationStore } from '@/lib/store';
import { TOUR_RESTORE_PANEL_EVENT } from '@/lib/tour-config';
import TourGuide, { RecordingTranscriptTour } from './TourGuide';

// Unlike TourGuide.test.tsx, these run the real driver.js: what is under test
// is how each of its ways of closing a tour reaches the hook (#915).
const nav = vi.hoisted(() => ({ pathname: '/' }));
vi.mock('next/navigation', () => ({ usePathname: () => nav.pathname }));
vi.mock('@/lib/api', () => ({ getUserMe: vi.fn() }));

const USER = 7;
const tours: {
  name: string;
  pathname: string;
  page: () => ReactElement;
  seen: () => boolean | undefined;
}[] = [
  {
    name: 'dashboard',
    pathname: '/',
    page: () => <><div id="nav-dashboard" /><div id="nav-recordings" /><TourGuide /></>,
    seen: () => useNavigationStore.getState().hasSeenTour[USER],
  },
  {
    name: 'recordings',
    pathname: '/recordings',
    page: () => <><div id="nav-recordings" /><div id="recordings-landing-panel" /><TourGuide /></>,
    seen: () => useNavigationStore.getState().hasSeenRecordingsTour[USER],
  },
  {
    name: 'transcript',
    pathname: '/recordings/rec-1',
    page: () => <>
      <div id="transcript-view" /><div id="audio-player" />
      <RecordingTranscriptTour recordingId="rec-1" eligible />
    </>,
    seen: () => useNavigationStore.getState().hasSeenTranscriptTour[USER],
  },
];

const popover = () => document.querySelector('.driver-popover');
const button = (selector: string) => {
  const found = document.querySelector<HTMLElement>(selector);
  if (!found) throw new Error(`no ${selector} on the tour`);
  return found;
};
const advance = async (ms: number) => {
  await act(async () => { await vi.advanceTimersByTimeAsync(ms); });
};
const open = async () => {
  // The account lookup resolves first, then the tour waits 1.5 s.
  await act(async () => {});
  await advance(1500);
  expect(popover()).not.toBeNull();
};

// driver.js animates a step in for 400 ms. A close inside that window used to
// end the tour without calling onDestroyed, so it was never marked seen.
const timings: [string, () => Promise<void>][] = [
  ['as soon as it opens', async () => {}],
  ['once the step has settled', () => advance(500)],
];
const closes: [string, (settle: () => Promise<void>) => Promise<void>][] = [
  ['Escape', async () => {
    act(() => { fireEvent.keyUp(document.activeElement ?? document.body, { key: 'Escape' }); });
  }],
  ['the close button', async () => {
    act(() => { fireEvent.click(button('.driver-popover-close-btn')); });
  }],
  ['Done', async (settle) => {
    for (let step = 0; popover() && step < 10; step++) {
      act(() => { fireEvent.click(button('.driver-popover-next-btn')); });
      await settle();
    }
  }],
];

describe.each(tours)('the $name tour', (tour) => {
  let restored: Mock<(event: Event) => void>;
  beforeEach(() => {
    vi.useFakeTimers();
    nav.pathname = tour.pathname;
    vi.mocked(getUserMe).mockReset().mockResolvedValue({ id: USER } as Awaited<ReturnType<typeof getUserMe>>);
    useNavigationStore.setState({ hasSeenTour: {}, hasSeenRecordingsTour: {}, hasSeenTranscriptTour: {} });
    restored = vi.fn<(event: Event) => void>();
    window.addEventListener(TOUR_RESTORE_PANEL_EVENT, restored);
  });
  afterEach(() => {
    window.removeEventListener(TOUR_RESTORE_PANEL_EVENT, restored);
    vi.useRealTimers();
  });

  describe.each(timings)('closed %s', (_, settle) => {
    it.each(closes)('with %s is marked seen and stays closed', async (_, close) => {
      const view = render(tour.page());
      await open();
      await settle();
      await close(settle);

      expect(popover()).toBeNull();
      expect(document.body.classList.contains('driver-active')).toBe(false);
      expect(tour.seen()).toBe(true);
      // The transcript tour's page puts back the panels it found, once.
      expect(restored).toHaveBeenCalledTimes(tour.name === 'transcript' ? 1 : 0);

      view.rerender(tour.page());
      await advance(5000);
      expect(popover()).toBeNull();
    });

    it('is left for a later visit when the page goes mid-tour', async () => {
      const view = render(tour.page());
      await open();
      await settle();
      if (tour.name === 'transcript') view.unmount();
      else {
        nav.pathname = '/tasks';
        view.rerender(tour.page());
      }

      expect(popover()).toBeNull();
      expect(document.body.classList.contains('driver-active')).toBe(false);
      expect(tour.seen()).toBeUndefined();
      expect(restored).toHaveBeenCalledTimes(tour.name === 'transcript' ? 1 : 0);

      nav.pathname = tour.pathname;
      if (tour.name === 'transcript') render(tour.page());
      else view.rerender(tour.page());
      await open();
    });
  });

  // A click aimed at the page as the tour opens lands on its overlay (#984).
  describe('a click on the overlay', () => {
    const clickOverlay = () => {
      const overlay = document.querySelector('svg.driver-overlay path');
      if (!overlay) throw new Error('no overlay on the page');
      act(() => { fireEvent.click(overlay); });
    };

    it.each([
      ['as the overlay fades in', 20],
      ['just inside the first second', 990],
    ])('made %s leaves the tour open and unseen', async (_, after) => {
      const view = render(tour.page());
      await open();
      await advance(after);
      clickOverlay();

      expect(popover()).not.toBeNull();
      expect(document.body.classList.contains('driver-active')).toBe(true);
      expect(tour.seen()).toBeUndefined();
      expect(restored).not.toHaveBeenCalled();

      // The tour still closes on purpose, and is then marked seen.
      act(() => { fireEvent.click(button('.driver-popover-close-btn')); });
      expect(popover()).toBeNull();
      expect(tour.seen()).toBe(true);
      view.rerender(tour.page());
      await advance(5000);
      expect(popover()).toBeNull();
    });

    it.each([
      ['once the first second has passed', 1000, false],
      ['after an early click was ignored', 1000, true],
    ])('made %s closes the tour and marks it seen', async (_, after, earlyClick) => {
      const view = render(tour.page());
      await open();
      if (earlyClick) {
        await advance(20);
        clickOverlay();
        await advance(after - 20);
      } else await advance(after);
      clickOverlay();

      expect(popover()).toBeNull();
      expect(document.body.classList.contains('driver-active')).toBe(false);
      expect(document.querySelector('svg.driver-overlay')).toBeNull();
      expect(tour.seen()).toBe(true);
      expect(restored).toHaveBeenCalledTimes(tour.name === 'transcript' ? 1 : 0);

      view.rerender(tour.page());
      await advance(5000);
      expect(popover()).toBeNull();
    });
  });
});
