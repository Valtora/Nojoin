import type { ComponentPropsWithoutRef } from "react";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CaptureProvider } from "@/lib/capture/CaptureProvider";
import { audioTrack, mediaStream } from "@/lib/capture/releaseMicrophone.fixtures";
import { clearPausedCaptureContext } from "@/lib/capture/shared";
import { createSegmentUploader } from "@/lib/capture/uploader";
import { AudioClock } from "@/lib/notes/audioClock";
import { useNotificationStore } from "@/lib/notificationStore";
import { ClientStatus, RecordingStatus, type Recording } from "@/types";

import CaptureShell from "./CaptureShell";
import MeetingControls from "./MeetingControls";
import RecordingCaptureSlot from "./RecordingCaptureSlot";

// Which tab gets the header's transport, with the real provider, controller,
// recovery dialog, slot and live controls. Browser media and the network are
// the edges, as in CaptureShell.resumeOwnership.test.tsx.
const api = vi.hoisted(() => ({
  getUnfinishedCaptures: vi.fn(),
  initRecording: vi.fn(),
  resumeRecordingCapture: vi.fn(),
  pauseRecordingCapture: vi.fn(),
  finalizeRecordingCapture: vi.fn(),
  discardRecordingCapture: vi.fn(),
  getRecording: vi.fn(),
  reportRecordingCaptureSources: vi.fn(),
}));
const pickCaptureSource = vi.hoisted(() => vi.fn());
let pathname = "/recordings/rec-1";

vi.mock("@/lib/api", async (importOriginal) => ({
  ...await importOriginal<typeof import("@/lib/api")>(),
  ...api,
}));
vi.mock("@/lib/capture/featureDetect", async (importOriginal) => ({
  ...await importOriginal<typeof import("@/lib/capture/featureDetect")>(),
  detectCaptureSupport: () => ({ supported: true, mode: "microphone_only" }),
}));
vi.mock("@/lib/capture/pickSource", async (importOriginal) => ({
  ...await importOriginal<typeof import("@/lib/capture/pickSource")>(),
  pickCaptureSource,
}));
vi.mock("@/lib/capture/mixer", () => ({
  createCaptureMixer: vi.fn(async () => ({
    pcmSource: {}, systemAnalyser: {}, microphoneAnalyser: {}, mixedAnalyser: {},
    detachMicrophone: vi.fn(), replaceMicrophone: vi.fn(), resumeContext: vi.fn(async () => true),
    applySettings: vi.fn(), updateAutomaticGain: vi.fn(), dispose: vi.fn(async () => {}),
  })),
}));
vi.mock("@/lib/capture/recorder", () => ({
  CAPTURE_SEGMENT_SECONDS: 2,
  createBrowserRecorder: vi.fn(() => ({
    start: vi.fn(), pause: vi.fn(async () => {}), resume: vi.fn(), stop: vi.fn(async () => {}),
  })),
}));
vi.mock("@/lib/capture/uploader", () => ({
  createSegmentUploader: vi.fn(() => ({
    enqueue: vi.fn(), restore: vi.fn(), recover: vi.fn(), waitForIdle: vi.fn(async () => {}),
    dispose: vi.fn(), settled: vi.fn(async () => {}),
    pendingSegmentCount: vi.fn(() => 0), pendingAudioSeconds: vi.fn(() => 0),
  })),
}));
vi.mock("@/lib/capture/waveform", async (importOriginal) => ({
  ...await importOriginal<typeof import("@/lib/capture/waveform")>(),
  createWaveformMonitor: vi.fn(() => ({ start: vi.fn(), stop: vi.fn() })),
}));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
  usePathname: () => pathname,
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock("./SessionSignInDialog", () => ({ default: () => null }));
vi.mock("next/link", () => ({
  default: ({ children, ...props }: ComponentPropsWithoutRef<"a">) => <a {...props}>{children}</a>,
}));

const base = { id: "rec-1", name: "Weekly sync", duration_seconds: 0 } as Recording;
// The server never heard the old page's pause, and the capture is not stalled.
const stillUploading = {
  ...base, status: RecordingStatus.UPLOADING, client_status: ClientStatus.RECORDING, capture_stalled: false,
  capture_epoch: 3,
} as Recording;
const lockQuery = vi.fn(async () => ({ held: [] as LockInfo[], pending: [] as LockInfo[] }));

const slot = () => screen.getByLabelText("Recording status and controls");

beforeEach(() => {
  vi.clearAllMocks();
  useNotificationStore.setState({ activeNotifications: [], history: [] });
  clearPausedCaptureContext();
  lockQuery.mockResolvedValue({ held: [], pending: [] });
  vi.stubGlobal("navigator", {
    locks: { query: lockQuery, request: (_name: string, _options: unknown, hold: () => Promise<void>) => hold() },
    // The beacon is queued as the page unloads but never reaches the server.
    sendBeacon: vi.fn(() => true),
  });
  api.initRecording.mockResolvedValue({ id: base.id, name: base.name, capture_epoch: 3 });
  api.finalizeRecordingCapture.mockResolvedValue({ id: base.id, status: "QUEUED" });
  api.discardRecordingCapture.mockResolvedValue(undefined);
  api.getRecording.mockResolvedValue({ id: base.id, status: "UPLOADING", captured_audio_seconds: 0 });
  api.reportRecordingCaptureSources.mockResolvedValue(undefined);
  pickCaptureSource.mockImplementation(async () => ({
    mode: "microphone_only",
    displayStream: null,
    microphoneStream: mediaStream(audioTrack()),
    captureReport: {
      mode: "microphone_only", requested_microphone_device_id: null, requested_microphone_label: null,
      available_microphones: [], browser_microphone_track: null, browser_display_audio_track: null,
      browser_display_video_track: null, shared_audio_available: false, configured_microphone_gain: 1,
      configured_system_gain: 1, configured_echo_cancellation: true, configured_noise_suppression: true,
      configured_auto_gain_control: true, notes: [],
    },
    release: vi.fn(),
  }));
  vi.spyOn(console, "info").mockImplementation(() => {});
  vi.spyOn(console, "warn").mockImplementation(() => {});
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  clearPausedCaptureContext();
});

/**
 * Starts a capture at epoch 3, then reloads the page while it records. With
 * `uploaded: false` the tab was offline, so nothing reached the server.
 */
const recordThenReload = async ({ uploaded = true } = {}) => {
  pathname = "/";
  api.getUnfinishedCaptures.mockResolvedValue([]);
  const oldPage = render(
    <CaptureProvider><CaptureShell><MeetingControls variant="dashboard" /></CaptureShell></CaptureProvider>,
  );
  await act(async () => {});
  fireEvent.click(screen.getByRole("button", { name: "Start Meeting" }));
  await screen.findByTitle("Pause recording");
  if (uploaded) act(() => vi.mocked(createSegmentUploader).mock.calls.at(-1)?.[0].onUploaded?.(0));
  await act(async () => { window.dispatchEvent(new Event("pagehide")); });
  oldPage.unmount();
  pathname = `/recordings/${base.id}`;
};

const renderSlot = (recording: Recording, audioClock?: AudioClock) => render(
  <CaptureProvider><CaptureShell>
    <RecordingCaptureSlot recording={recording} audioClock={audioClock} />
  </CaptureShell></CaptureProvider>,
);

describe("the header of a tab reloaded while it records", () => {
  it("holds Stop and Discard while the tab holds the recording's recovery offer", async () => {
    await recordThenReload();
    api.getUnfinishedCaptures.mockResolvedValue([stillUploading]);
    renderSlot(stillUploading);
    await screen.findByRole("dialog");

    expect(within(slot()).getByTitle("Discard recording")).toBeEnabled();
    fireEvent.click(within(slot()).getByTitle("Stop recording"));

    await waitFor(() => expect(api.finalizeRecordingCapture).toHaveBeenCalledOnce());
    expect(api.finalizeRecordingCapture.mock.calls[0][0]).toBe(base.id);
  });

  it("resumes from the header's Resume as from the dialog's, with no runtime", async () => {
    await recordThenReload();
    api.getUnfinishedCaptures.mockResolvedValue([stillUploading]);
    // The server takes over a capture still uploading only for the epoch its
    // tab held, and refuses anyone else until two minutes have passed.
    api.resumeRecordingCapture.mockImplementation(async (_id: string, options: { reclaimEpoch?: number }) => {
      if (options.reclaimEpoch !== 3) {
        throw new Error("This recording is still in progress and received audio in the last two minutes.");
      }
      return { recording_id: base.id, status: "UPLOADING", last_sequence: 0, capture_epoch: 4 };
    });
    renderSlot(stillUploading);
    await screen.findByRole("dialog");

    fireEvent.click(within(slot()).getByTitle("Resume recording"));

    await waitFor(() => expect(within(slot()).getByTitle("Pause recording")).toBeEnabled());
    expect(api.resumeRecordingCapture).toHaveBeenCalledOnce();
    expect(useNotificationStore.getState().activeNotifications.filter(({ type }) => type === "error")).toEqual([]);
  });

  it("shows the server's state, without transport, once another device has taken the capture over", async () => {
    // Reloaded offline before the first upload, then resumed and recording on
    // another device: the tab still has the ID it stored, but no offer.
    await recordThenReload({ uploaded: false });
    const takenOver = { ...stillUploading, capture_epoch: 4 } as Recording;
    api.getUnfinishedCaptures.mockResolvedValue([takenOver]);
    renderSlot(takenOver);
    await act(async () => {});
    await act(async () => {});

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(within(slot()).getByText("Live")).toBeInTheDocument();
    expect(within(slot()).queryByText("Paused")).not.toBeInTheDocument();
    expect(within(slot()).queryByTitle("Stop recording")).not.toBeInTheDocument();
    expect(within(slot()).queryByTitle("Resume recording")).not.toBeInTheDocument();
  });
});

describe("the header of a tab that is not capturing", () => {
  it("runs the server's audio clock while live and shows its reading while paused", async () => {
    let now = 0;
    const audioClock = new AudioClock(() => now);
    audioClock.observe({ ...stillUploading, live_audio_ms: 65_900 });
    api.getUnfinishedCaptures.mockResolvedValue([stillUploading]);
    // Another tab of this browser holds the capture, so this one is offered nothing.
    lockQuery.mockResolvedValue({ held: [{ name: `nojoin-capture:${base.id}`, mode: "shared" }], pending: [] });
    const view = renderSlot(stillUploading, audioClock);
    await act(async () => {});

    // `duration_seconds` is still 0: the clock is the server's audio, in whole
    // seconds heard, as the live controls count them (65.9 s reads 01:05).
    expect(within(slot()).getByText("Live")).toBeInTheDocument();
    expect(within(slot()).getByText("01:05")).toBeInTheDocument();
    now = 3_000;
    await waitFor(() => expect(within(slot()).getByText("01:08")).toBeInTheDocument(), { timeout: 2_000 });
    expect(within(slot()).queryByTitle("Stop recording")).not.toBeInTheDocument();

    const paused = { ...stillUploading, status: RecordingStatus.PAUSED, client_status: ClientStatus.PAUSED };
    audioClock.observe({ ...paused, live_audio_ms: 70_000 });
    view.rerender(<CaptureProvider><CaptureShell>
      <RecordingCaptureSlot recording={paused} audioClock={audioClock} />
    </CaptureShell></CaptureProvider>);
    await waitFor(() => expect(within(slot()).getByText("01:10")).toBeInTheDocument(), { timeout: 2_000 });
    expect(within(slot()).getByText("Paused")).toBeInTheDocument();
  });
});
