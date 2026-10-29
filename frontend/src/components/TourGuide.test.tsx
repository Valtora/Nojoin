import { act, cleanup, render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { getUserMe } from '@/lib/api';
import { useNavigationStore } from '@/lib/store';
import TourGuide, { RecordingTranscriptTour } from './TourGuide';

const tour = vi.hoisted(() => ({
  pathname: '/recordings/rec-1',
  instances: [] as { drive: ReturnType<typeof vi.fn>; destroy: ReturnType<typeof vi.fn>; finish: () => void }[],
}));

vi.mock('next/navigation', () => ({ usePathname: () => tour.pathname }));
vi.mock('@/lib/api', () => ({ getUserMe: vi.fn() }));
vi.mock('driver.js', () => ({
  driver: vi.fn((options: { onDestroyed: () => void }) => {
    const instance = {
      drive: vi.fn(),
      destroy: vi.fn(() => options.onDestroyed()),
      finish: () => options.onDestroyed(),
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
