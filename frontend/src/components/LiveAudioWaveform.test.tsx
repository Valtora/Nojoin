import { Profiler } from "react";
import { act, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DEFAULT_CAPTURE_LEVELS, type CaptureLevels } from "@/lib/capture/shared";

import LiveAudioWaveform from "./LiveAudioWaveform";

const meter = vi.hoisted(() => {
  let levels = { system: 0, microphone: 0, mixed: 0 };
  const listeners = new Set<(state: { levels: typeof levels }) => void>();
  return {
    reset() {
      levels = { system: 0, microphone: 0, mixed: 0 };
      listeners.clear();
    },
    sample(next: typeof levels) {
      levels = next;
      listeners.forEach((listener) => listener({ levels }));
    },
    controller: {
      getState: () => ({ levels }),
      subscribe: (listener: (state: { levels: typeof levels }) => void) => {
        listeners.add(listener);
        return () => listeners.delete(listener);
      },
    },
  };
});

vi.mock("@/lib/capture/CaptureProvider", () => ({
  useCapture: () => ({
    controller: meter.controller,
    runtimeActive: true,
    support: { supported: true, mode: "shared_audio" },
  }),
}));

const level = (mixed: number): CaptureLevels => ({ ...DEFAULT_CAPTURE_LEVELS, mixed });

function renderWaveform(liveTextMark: string | null = null) {
  meter.reset();
  let commits = 0;
  const tree = (mark: string | null) => (
    <Profiler id="waveform" onRender={() => { commits += 1; }}>
      <LiveAudioWaveform recordingId="rec-1" enabled compact liveTextMark={mark} />
    </Profiler>
  );
  const { rerender } = render(tree(liveTextMark));
  return { commits: () => commits, setLiveTextMark: (mark: string) => rerender(tree(mark)) };
}

afterEach(() => {
  vi.useRealTimers();
});

describe("LiveAudioWaveform", () => {
  it("draws meter samples on the bars without rendering", () => {
    const { commits } = renderWaveform();
    const before = commits();

    act(() => {
      for (let frame = 0; frame < 120; frame += 1) meter.sample(level(frame % 2 ? 60 : 20));
    });

    expect(commits()).toBe(before);
    const bars = screen.getAllByTestId("audio-bar");
    expect(bars).toHaveLength(48);
    // The newest sample is the last bar, and the meter has moved off rest.
    expect(bars[47].style.height).not.toBe("6%");
  });

  it("still shows the quiet hint once the audio has been quiet for 20 s", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-10-29T10:00:00Z"));
    renderWaveform();

    act(() => meter.sample(level(40)));
    expect(screen.queryByText("Audio has been quiet for a bit")).toBeNull();

    vi.setSystemTime(new Date("2026-10-29T10:00:21Z"));
    act(() => meter.sample(level(2)));

    expect(screen.getByText("Audio has been quiet for a bit")).toBeTruthy();
  });

  it("takes new live text as audio getting through, and drops the quiet hint", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-10-29T10:00:00Z"));
    const { setLiveTextMark } = renderWaveform("3:12.5");

    vi.setSystemTime(new Date("2026-10-29T10:00:21Z"));
    act(() => meter.sample(level(2)));
    expect(screen.getByText("Audio has been quiet for a bit")).toBeTruthy();

    act(() => setLiveTextMark("4:15.1"));
    expect(screen.queryByText("Audio has been quiet for a bit")).toBeNull();

    // Quiet audio alone brings it back only 20 s after the text.
    vi.setSystemTime(new Date("2026-10-29T10:00:30Z"));
    act(() => meter.sample(level(3)));
    expect(screen.queryByText("Audio has been quiet for a bit")).toBeNull();
  });
});
