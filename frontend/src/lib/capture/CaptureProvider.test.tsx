import { useEffect } from "react";
import { act, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { CaptureProvider, useCapture, useCaptureState } from "./CaptureProvider";
import { DEFAULT_CAPTURE_LEVELS, type CaptureState } from "./shared";

// A controller reduced to its store: the provider only reads and subscribes.
const store = vi.hoisted(() => {
  let state = {} as CaptureState;
  const listeners = new Set<(state: CaptureState) => void>();
  return {
    reset(initial: CaptureState) {
      state = initial;
      listeners.clear();
    },
    set(patch: Partial<CaptureState>) {
      state = { ...state, ...patch };
      listeners.forEach((listener) => listener(state));
    },
    controller: {
      getState: () => state,
      subscribe: (listener: (state: CaptureState) => void) => {
        listeners.add(listener);
        return () => listeners.delete(listener);
      },
      attachLifecycle: () => {},
      updateRouteSignature: () => {},
      refreshPausedRecording: async () => {},
      openRecording: async () => {},
      clearError: () => {},
      destroy: async () => {},
    },
  };
});

vi.mock("./controller", () => ({ createCaptureController: () => store.controller }));
vi.mock("next/navigation", () => ({
  usePathname: () => "/recordings/rec-1",
  useSearchParams: () => new URLSearchParams(),
}));

const initialState = (): CaptureState => ({
  status: "recording",
  support: { supported: true, mode: "shared_audio" },
  levels: DEFAULT_CAPTURE_LEVELS,
  error: null,
  lastSequence: 0,
  elapsedSeconds: 0,
  recordingId: "rec-1",
  pausedRecording: null,
  attentionRecordings: [],
  runtimeActive: true,
  ownedRecordingId: "rec-1",
  settings: {} as CaptureState["settings"],
  finalizeRetry: null,
  stopStage: null,
  coverageWarning: null,
  coverageWarningDismissedAt: null,
  uploadQueue: { segments: 0, seconds: 0 },
});

// Each consumer counts its own renders, from an effect that runs after every one.
const renders = { capture: 0, elapsed: 0 };

function CaptureConsumer() {
  const { status } = useCapture();
  useEffect(() => {
    renders.capture += 1;
  });
  return <p>status {status}</p>;
}

function ElapsedConsumer() {
  const elapsedSeconds = useCaptureState((state) => state.elapsedSeconds);
  useEffect(() => {
    renders.elapsed += 1;
  });
  return <p>elapsed {elapsedSeconds}</p>;
}

/** Renders one consumer of each kind and returns the counts after mounting. */
function setup() {
  store.reset(initialState());
  renders.capture = 0;
  renders.elapsed = 0;
  render(
    <CaptureProvider>
      <CaptureConsumer />
      <ElapsedConsumer />
    </CaptureProvider>,
  );
  return { renders: { ...renders }, current: renders };
}

describe("CaptureProvider", () => {
  it("does not re-render useCapture consumers for meter samples, ticks or uploads", () => {
    const { renders, current } = setup();

    act(() => {
      for (let sample = 1; sample <= 120; sample += 1) {
        store.set({ levels: { system: sample % 100, microphone: 0, mixed: sample % 100 } });
      }
      store.set({ elapsedSeconds: 1 });
      store.set({ uploadQueue: { segments: 1, seconds: 2 } });
      store.set({ lastSequence: 0, uploadQueue: { segments: 0, seconds: 0 } });
    });

    // The meter's 120 frames, a tick and an upload: none reach useCapture().
    expect(current.capture).toBe(renders.capture);
    // The elapsed time reaches only the component that reads it, once.
    expect(current.elapsed).toBe(renders.elapsed + 1);
    expect(screen.getByText("elapsed 1")).toBeTruthy();
  });

  it("re-renders useCapture consumers when the capture itself changes", () => {
    const { renders, current } = setup();

    act(() => store.set({ status: "paused" }));

    expect(screen.getByText("status paused")).toBeTruthy();
    expect(current.capture).toBe(renders.capture + 1);
    expect(current.elapsed).toBe(renders.elapsed);
  });
});
