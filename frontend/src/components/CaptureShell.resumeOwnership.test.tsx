import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CaptureProvider } from "@/lib/capture/CaptureProvider";
import { audioTrack, mediaStream } from "@/lib/capture/releaseMicrophone.fixtures";
import { clearPausedCaptureContext } from "@/lib/capture/shared";
import { ClientStatus, RecordingStatus, type Recording } from "@/types";

import CaptureShell from "./CaptureShell";
import LiveMeetingControls from "./LiveMeetingControls";
import MeetingControls from "./MeetingControls";

// Browser media and the network are the edges: the provider, controller,
// recovery dialog and controls are the real ones.
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
vi.mock("@/lib/capture/featureDetect", () => ({
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
vi.mock("@/lib/capture/waveform", () => ({
  createWaveformMonitor: vi.fn(() => ({ start: vi.fn(), stop: vi.fn() })),
}));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
  usePathname: () => pathname,
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock("./SessionSignInDialog", () => ({ default: () => null }));

const paused = {
  id: "rec-1",
  name: "Weekly sync",
  status: RecordingStatus.PAUSED,
  client_status: ClientStatus.PAUSED,
  capture_epoch: 3,
} as Recording;
const lifecycle = (status: string) => ({
  recording_id: paused.id, status, last_sequence: 4, capture_epoch: 4,
});

beforeEach(() => {
  vi.clearAllMocks();
  clearPausedCaptureContext();
  pathname = `/recordings/${paused.id}`;
  vi.stubGlobal("navigator", {
    locks: {
      query: async () => ({ held: [], pending: [] }),
      request: (_name: string, _options: unknown, hold: () => Promise<void>) => hold(),
    },
  });
  api.getUnfinishedCaptures.mockResolvedValue([paused]);
  api.resumeRecordingCapture.mockImplementation(async () => {
    // The server lists the recording as live once it has resumed.
    api.getUnfinishedCaptures.mockResolvedValue([
      { ...paused, status: RecordingStatus.UPLOADING, client_status: ClientStatus.RECORDING },
    ]);
    return lifecycle("UPLOADING");
  });
  api.pauseRecordingCapture.mockResolvedValue(lifecycle("PAUSED"));
  api.initRecording.mockResolvedValue({ id: paused.id, name: paused.name, capture_epoch: 3 });
  api.finalizeRecordingCapture.mockResolvedValue({ id: paused.id, status: "QUEUED" });
  api.getRecording.mockResolvedValue({ id: paused.id, status: "UPLOADING", captured_audio_seconds: 0 });
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

describe("a recording claimed and resumed from the recovery dialog", () => {
  it("can be stopped and discarded from the page it was resumed on", async () => {
    // The recording page is already mounted, with no capture of its own, when
    // this tab takes the recording over.
    render(<CaptureProvider><CaptureShell>
      <LiveMeetingControls size="bar" serverPaused />
    </CaptureShell></CaptureProvider>);
    await act(async () => {});
    expect(screen.getByTitle("Stop recording")).toBeDisabled();

    fireEvent.click(await screen.findByRole("button", { name: /Resume recording/ }));

    await waitFor(() => expect(screen.getByTitle("Pause recording")).toBeEnabled());
    expect(api.resumeRecordingCapture).toHaveBeenCalledWith(paused.id, { epoch: undefined });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(screen.getByTitle("Stop recording")).toBeEnabled();
    expect(screen.getByTitle("Discard recording")).toBeEnabled();
    expect(screen.getByText("· this tab")).toBeInTheDocument();

    fireEvent.click(screen.getByTitle("Stop recording"));

    await waitFor(() => expect(api.finalizeRecordingCapture).toHaveBeenCalledOnce());
    expect(api.finalizeRecordingCapture.mock.calls[0][0]).toBe(paused.id);
  });
});

describe("a recording paused and resumed in the tab capturing it", () => {
  it("can still be stopped and discarded", async () => {
    pathname = "/";
    api.getUnfinishedCaptures.mockResolvedValue([]);
    render(<CaptureProvider><CaptureShell><MeetingControls variant="dashboard" /></CaptureShell></CaptureProvider>);
    await act(async () => {});

    fireEvent.click(screen.getByRole("button", { name: "Start Meeting" }));
    fireEvent.click(await screen.findByTitle("Pause recording"));
    fireEvent.click(await screen.findByTitle("Resume recording"));

    await waitFor(() => expect(screen.getByTitle("Pause recording")).toBeEnabled());
    expect(api.resumeRecordingCapture).toHaveBeenCalledWith(paused.id, { epoch: 3 });
    expect(screen.getByTitle("Stop recording")).toBeEnabled();
    expect(screen.getByTitle("Discard recording")).toBeEnabled();

    fireEvent.click(screen.getByTitle("Stop recording"));

    await waitFor(() => expect(api.finalizeRecordingCapture).toHaveBeenCalledOnce());
    expect(api.finalizeRecordingCapture.mock.calls[0][0]).toBe(paused.id);
  });
});
