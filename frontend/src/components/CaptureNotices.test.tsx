import { act, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { CaptureProvider } from "@/lib/capture/CaptureProvider";
import { DEFAULT_CAPTURE_LEVELS, type CaptureState } from "@/lib/capture/shared";

import { CaptureNotices } from "./LiveMeetingControls";

// The real provider over a controller reduced to its store. The provider keeps
// the upload queue out of useCapture() (issue #822), so a notice that read it
// from there would never see the backlog below.
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

vi.mock("@/lib/capture/controller", () => ({ createCaptureController: () => store.controller }));
vi.mock("next/navigation", () => ({
  usePathname: () => "/recordings",
  useSearchParams: () => new URLSearchParams(),
}));

const recording = (): CaptureState => ({
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

describe("CaptureNotices", () => {
  it("shows an upload backlog as it builds, though only the queue changed", () => {
    store.reset(recording());
    render(
      <CaptureProvider>
        <CaptureNotices />
      </CaptureProvider>,
    );
    expect(screen.queryByText(/Uploads are .* behind\./)).toBeNull();

    act(() => store.set({ uploadQueue: { segments: 20, seconds: 40 } }));

    expect(screen.getByText("Uploads are 00:40 behind.")).toBeTruthy();
  });
});
