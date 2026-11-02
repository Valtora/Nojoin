import { act, cleanup, render } from '@testing-library/react';
import type { ReactElement } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { getUserMe } from '@/lib/api';
import { useNavigationStore } from '@/lib/store';
import { TOUR_RESTORE_PANEL_EVENT } from '@/lib/tour-config';
import TourGuide, { RecordingTranscriptTour } from './TourGuide';

const tour = vi.hoisted(() => ({
  pathname: '/recordings/rec-1',
  instances: [] as {
    drive: ReturnType<typeof vi.fn>;
    destroy: ReturnType<typeof vi.fn>;
    finish: () => void;
    steps: { element?: unknown }[];
  }[],
}));

vi.mock('next/navigation', () => ({ usePathname: () => tour.pathname }));
vi.mock('@/lib/api', () => ({ getUserMe: vi.fn() }));
vi.mock('driver.js', () => ({
  driver: vi.fn((options: { onDestroyStarted: () => void; steps: { element?: unknown }[] }) => {
    const instance = {
      drive: vi.fn(),
      destroy: vi.fn(),
      // A person closing the tour: driver.js hands the close to the hook,
      // which destroys it. TourGuide.close.test.tsx runs the real library.
      finish: () => options.onDestroyStarted(),
      steps: options.steps,
    };
    tour.instances.push(instance);
    return instance;
  }),
}));

const account = { id: 7 } as Awaited<ReturnType<typeof getUserMe>>;
const detail = (eligible: boolean, recordingId = 'rec-1') => <>
  <div id="transcript-view" />
  <RecordingTranscriptTour recordingId={recordingId} eligible={eligible} />
</>;
const tick = async (ms = 1500) => {
  // Flush the account response and its React effect before advancing the delay.
  await act(async () => {});
  await act(async () => { await vi.advanceTimersByTimeAsync(ms); });
};

describe('recording transcript tour', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    tour.instances.length = 0;
    tour.pathname = '/recordings/rec-1';
    vi.mocked(getUserMe).mockReset().mockResolvedValue(account);
    useNavigationStore.setState({ hasSeenTour: {}, hasSeenRecordingsTour: {}, hasSeenTranscriptTour: {} });
  });
  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  it('waits for completion on the same recording, then remembers a user-completed tour', async () => {
    const view = render(detail(false));
    await tick(5000);
    expect(tour.instances).toHaveLength(0);
    expect(getUserMe).not.toHaveBeenCalled();

    view.rerender(detail(true));
    await tick();
    expect(tour.instances[0].drive).toHaveBeenCalledOnce();
    act(() => tour.instances[0].finish());
    expect(useNavigationStore.getState().hasSeenTranscriptTour[7]).toBe(true);
    view.rerender(detail(false));
    view.rerender(detail(true));
    await tick();
    expect(tour.instances).toHaveLength(1);
  });

  it('removes an active overlay on reprocessing without consuming the tour', async () => {
    const view = render(detail(true));
    await tick();
    view.rerender(detail(false));
    expect(tour.instances[0].destroy).toHaveBeenCalledOnce();
    expect(useNavigationStore.getState().hasSeenTranscriptTour[7]).toBeUndefined();
    view.rerender(detail(true));
    await tick();
    expect(tour.instances[1].drive).toHaveBeenCalledOnce();
  });

  it('destroys the previous recording tour and gives the next recording its own delay', async () => {
    const view = render(detail(true));
    await tick();
    view.rerender(detail(true, 'rec-2'));
    expect(tour.instances[0].destroy).toHaveBeenCalledOnce();
    await tick(1499);
    expect(tour.instances).toHaveLength(1);
    await tick(1);
    expect(tour.instances[1].drive).toHaveBeenCalledOnce();
    expect(useNavigationStore.getState().hasSeenTranscriptTour[7]).toBeUndefined();
  });

  it('removes the overlay on route unmount without marking the tour seen', async () => {
    const view = render(detail(true));
    await tick();
    view.unmount();
    expect(tour.instances[0].destroy).toHaveBeenCalledOnce();
    expect(useNavigationStore.getState().hasSeenTranscriptTour[7]).toBeUndefined();
  });

  it.each([
    ['finishes', (view: ReturnType<typeof render>) => { act(() => tour.instances[0].finish()); void view; }],
    ['is cancelled with its page', (view: ReturnType<typeof render>) => view.unmount()],
  ])('asks the page to put its panel back when the tour %s', async (_, end) => {
    const restored = vi.fn();
    window.addEventListener(TOUR_RESTORE_PANEL_EVENT, restored);
    try {
      const view = render(detail(true));
      await tick();
      expect(restored).not.toHaveBeenCalled();
      end(view);
      expect(restored).toHaveBeenCalled();
    } finally {
      window.removeEventListener(TOUR_RESTORE_PANEL_EVENT, restored);
    }
  });

  it('does not consume a tour when no usable anchor exists', async () => {
    render(<RecordingTranscriptTour recordingId="rec-1" eligible />);
    await tick();
    expect(tour.instances).toHaveLength(0);
    expect(useNavigationStore.getState().hasSeenTranscriptTour[7]).toBeUndefined();
  });

  it('the global guide never starts a transcript tour from the URL alone', async () => {
    render(<><div id="transcript-view" /><TourGuide /></>);
    await tick(5000);
    expect(tour.instances).toHaveLength(0);
  });

  it.each([['/', 'nav-dashboard'], ['/recordings', 'nav-recordings']])(
    'retains the %s tour and cancels its overlay when leaving', async (pathname, anchor) => {
      tour.pathname = pathname;
      const view = render(<><div id={anchor} /><TourGuide /></>);
      await tick();
      expect(tour.instances[0].drive).toHaveBeenCalledOnce();
      tour.pathname = '/recordings/rec-1';
      view.rerender(<><div id={anchor} /><TourGuide /></>);
      expect(tour.instances[0].destroy).toHaveBeenCalledOnce();
      expect(useNavigationStore.getState().hasSeenTour[7]).toBeUndefined();
      expect(useNavigationStore.getState().hasSeenRecordingsTour[7]).toBeUndefined();
    },
  );
});

describe('a tour held back while someone types (#918)', () => {
  const pages = [
    { name: 'dashboard', pathname: '/', page: (extra?: ReactElement) => <><div id="nav-dashboard" />{extra}<TourGuide /></> },
    { name: 'recordings', pathname: '/recordings', page: (extra?: ReactElement) => <><div id="nav-recordings" />{extra}<TourGuide /></> },
    {
      name: 'transcript',
      pathname: '/recordings/rec-1',
      page: (extra?: ReactElement) => <>
        <div id="transcript-view" />{extra}<RecordingTranscriptTour recordingId="rec-1" eligible />
      </>,
    },
  ];
  const fields: [string, ReactElement][] = [
    ['an input', <input key="field" data-testid="field" type="search" />],
    ['a textarea', <textarea key="field" data-testid="field" />],
    ['a Tiptap-style editor', <div key="field" data-testid="field" className="ProseMirror" contentEditable
      suppressContentEditableWarning tabIndex={0} role="textbox" />],
  ];
  const field = () => document.querySelector<HTMLElement>('[data-testid="field"]')!;
  const type = (keys: number) => {
    for (let key = 0; key < keys; key++) field().dispatchEvent(new KeyboardEvent('keydown', { key: 'a', bubbles: true }));
  };
  const seen = () => {
    const state = useNavigationStore.getState();
    return state.hasSeenTour[7] ?? state.hasSeenRecordingsTour[7] ?? state.hasSeenTranscriptTour[7];
  };

  beforeEach(() => {
    vi.useFakeTimers();
    tour.instances.length = 0;
    vi.mocked(getUserMe).mockReset().mockResolvedValue(account);
    useNavigationStore.setState({ hasSeenTour: {}, hasSeenRecordingsTour: {}, hasSeenTranscriptTour: {} });
  });
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  describe.each(pages)('the $name tour', (page) => {
    beforeEach(() => { tour.pathname = page.pathname; });

    it.each(fields)('does not open while %s has focus, and opens once it is left and the page is quiet',
      async (_, input) => {
        render(page.page(input));
        act(() => field().focus());
        await tick();
        expect(tour.instances).toHaveLength(0);
        for (let second = 0; second < 5; second++) {
          type(4);
          await tick(1000);
        }
        expect(tour.instances).toHaveLength(0);
        expect(document.activeElement).toBe(field());

        act(() => field().blur());
        await tick(1499);
        expect(tour.instances).toHaveLength(0);
        await tick(1);
        expect(tour.instances[0].drive).toHaveBeenCalledOnce();
        expect(seen()).toBeUndefined();
      });

    it('opens at 1.5 s as before when nothing is being typed', async () => {
      render(page.page(<button key="field" data-testid="field">Filter</button>));
      act(() => field().focus());
      await tick(1499);
      expect(tour.instances).toHaveLength(0);
      await tick(1);
      expect(tour.instances[0].drive).toHaveBeenCalledOnce();
    });

    it('stays unseen when the page goes while it waits, and comes back on a later visit', async () => {
      const view = render(page.page(fields[0][1]));
      act(() => field().focus());
      await tick();
      if (page.name === 'transcript') view.unmount();
      else {
        tour.pathname = '/tasks';
        view.rerender(page.page(fields[0][1]));
      }
      act(() => field()?.blur());
      await tick(10_000);
      expect(tour.instances).toHaveLength(0);
      expect(seen()).toBeUndefined();
      expect(vi.getTimerCount()).toBe(0);

      tour.pathname = page.pathname;
      if (page.name === 'transcript') render(page.page());
      else view.rerender(page.page());
      await tick();
      expect(tour.instances[0].drive).toHaveBeenCalledOnce();
    });

    it('removes its listeners and timer when the page goes while it waits', async () => {
      const added = vi.spyOn(document, 'addEventListener');
      const removed = vi.spyOn(document, 'removeEventListener');
      const view = render(page.page(fields[0][1]));
      act(() => field().focus());
      await tick();
      const typing = ['focusin', 'focusout', 'keydown', 'pointerdown', 'compositionstart', 'compositionend'];
      const listeners = added.mock.calls
        .filter(([event]) => typing.includes(event))
        .map(([event, listener]) => [event, listener]);
      expect(listeners.map(([event]) => event).sort()).toEqual([...typing].sort());

      if (page.name === 'transcript') view.unmount();
      else {
        tour.pathname = '/tasks';
        view.rerender(page.page(fields[0][1]));
      }
      expect(vi.getTimerCount()).toBe(0);
      expect(removed.mock.calls.map(([event, listener]) => [event, listener])).toEqual(expect.arrayContaining(listeners));
    });
  });

  it('reads the steps when the tour opens, not when it was due', async () => {
    tour.pathname = '/';
    const view = render(<><div id="nav-dashboard" />{fields[0][1]}<TourGuide /></>);
    act(() => field().focus());
    await tick();
    view.rerender(<><div id="nav-dashboard" /><div id="nav-recordings" />{fields[0][1]}<TourGuide /></>);
    act(() => field().blur());
    await tick();
    expect(tour.instances[0].steps.map(step => step.element)).toContain('#nav-recordings');
  });
});
