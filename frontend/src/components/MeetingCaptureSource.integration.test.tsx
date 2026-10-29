import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useEffect } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CaptureProvider, useCapture } from "@/lib/capture/CaptureProvider";

import MeetingControls from "./MeetingControls";

// The real provider, controller, feature detection, settings storage and source
// picker run here. Only the browser's media APIs, the network and the audio
// graph (Web Audio, covered by the mixer's and recorder's own tests) are stubbed.

const routerPush = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: routerPush }),
  usePathname: () => "/",
  useSearchParams: () => new URLSearchParams(),
}));

const api = vi.hoisted(() => ({
  initRecording: vi.fn(),
  pauseRecordingCapture: vi.fn(),
  resumeRecordingCapture: vi.fn(),
  getUnfinishedCaptures: vi.fn(),
  reportRecordingCaptureSources: vi.fn(),
}));
vi.mock("@/lib/api", async (original) => ({
  ...(await original<typeof import("@/lib/api")>()),
  ...api,
}));

vi.mock("@/lib/capture/mixer", () => ({
  createCaptureMixer: vi.fn(async () => ({
    pcmSource: {},
    systemAnalyser: {},
    microphoneAnalyser: {},
    mixedAnalyser: {},
    updateAutomaticGain: vi.fn(),
    applySettings: vi.fn(),
    dispose: vi.fn(async () => {}),
  })),
}));
vi.mock("@/lib/capture/recorder", () => ({
  CAPTURE_SEGMENT_SECONDS: 2,
  createBrowserRecorder: vi.fn(() => ({
    start: vi.fn(),
    stop: vi.fn(async () => {}),
    pause: vi.fn(),
    resume: vi.fn(),
  })),
}));
vi.mock("@/lib/capture/uploader", () => ({
  createSegmentUploader: vi.fn(() => ({
    enqueue: vi.fn(),
    restore: vi.fn(),
    waitForIdle: vi.fn(async () => {}),
    recover: vi.fn(() => true),
    dispose: vi.fn(),
    settled: vi.fn(async () => {}),
    pendingSegmentCount: vi.fn(() => 0),
    pendingAudioSeconds: vi.fn(() => 0),
  })),
}));
vi.mock("@/lib/capture/waveform", () => ({
  createWaveformMonitor: vi.fn(() => ({ start: vi.fn(), stop: vi.fn() })),
}));
vi.mock("./LiveMeetingControls", () => ({
  default: () => <div data-testid="live-controls" />,
}));

const DESKTOP_CHROME =
  "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36";
const ANDROID_CHROME =
  "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Mobile Safari/537.36";

const track = (kind: "audio" | "video") =>
  Object.assign(new EventTarget(), {
    kind,
    label: kind,
    enabled: true,
    muted: false,
    readyState: "live",
    stop: vi.fn(),
    getSettings: () => ({}),
  }) as unknown as MediaStreamTrack;

const stream = (tracks: MediaStreamTrack[]) =>
  ({
    getTracks: () => tracks,
    getAudioTracks: () => tracks.filter((t) => t.kind === "audio"),
    getVideoTracks: () => tracks.filter((t) => t.kind === "video"),
  }) as unknown as MediaStream;

const media = {
  getDisplayMedia: vi.fn(async () => stream([track("audio"), track("video")])),
  getUserMedia: vi.fn(async () => stream([track("audio")])),
  enumerateDevices: vi.fn(async () => [] as MediaDeviceInfo[]),
};

const PAUSED = {
  id: "rec-1",
  name: "Meeting",
  status: "PAUSED",
  client_status: "PAUSED",
  capture_stalled: false,
};

let capture: ReturnType<typeof useCapture>;
function Probe() {
  const value = useCapture();
  useEffect(() => {
    capture = value;
  });
  return null;
}

/** Opens Nojoin in a tab; unmounting it is the tab reloading. */
const openTab = () =>
  render(
    <CaptureProvider>
      <MeetingControls />
      <Probe />
    </CaptureProvider>,
  );

const useBrowser = (userAgent: string) => {
  vi.spyOn(window.navigator, "userAgent", "get").mockReturnValue(userAgent);
};

const startFromTheButton = async () => {
  fireEvent.click(screen.getByRole("button", { name: "Start Meeting" }));
  await waitFor(() => expect(routerPush).toHaveBeenCalledWith("/recordings/rec-1"));
  expect(capture.status).toBe("recording");
};

/** Pauses and reloads the tab; the new page finds the paused recording. */
const pauseAndReload = async (tab: ReturnType<typeof openTab>) => {
  await act(() => capture.pause());
  api.getUnfinishedCaptures.mockResolvedValue([PAUSED]);
  tab.unmount();
  const reloaded = openTab();
  await waitFor(() => expect(capture.pausedRecording?.id).toBe("rec-1"));
  return reloaded;
};

const resumeThePausedRecording = async () => {
  await act(async () => {
    await capture.resume();
  });
  expect(capture.status).toBe("recording");
};

describe("the recording source chosen beside Start", () => {
  beforeEach(() => {
    routerPush.mockReset();
    media.getDisplayMedia.mockClear();
    media.getUserMedia.mockClear();
    Object.defineProperty(window.navigator, "mediaDevices", { value: media, configurable: true });
    vi.stubGlobal("AudioWorkletNode", class {});
    api.initRecording.mockResolvedValue({ id: "rec-1", name: "Meeting", capture_epoch: 0 });
    api.pauseRecordingCapture.mockResolvedValue({
      recording_id: "rec-1", status: "PAUSED", last_sequence: -1, capture_epoch: 0,
    });
    // The reloaded page takes the capture over, so the server moves its epoch on.
    api.resumeRecordingCapture.mockResolvedValue({
      recording_id: "rec-1", status: "UPLOADING", last_sequence: -1, capture_epoch: 1,
    });
    api.getUnfinishedCaptures.mockResolvedValue([]);
    api.reportRecordingCaptureSources.mockResolvedValue(undefined);
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("records only the microphone on desktop, at Start and on Resume after a reload", async () => {
    useBrowser(DESKTOP_CHROME);
    const tab = openTab();
    fireEvent.change(screen.getByRole("combobox", { name: "Recording source" }), {
      target: { value: "microphone_only" },
    });

    await startFromTheButton();
    expect(media.getUserMedia).toHaveBeenCalledOnce();
    expect(media.getDisplayMedia).not.toHaveBeenCalled();

    const reloaded = await pauseAndReload(tab);
    expect(screen.getByRole("combobox", { name: "Recording source" })).toHaveValue("microphone_only");
    await resumeThePausedRecording();
    expect(media.getUserMedia).toHaveBeenCalledTimes(2);
    expect(media.getDisplayMedia).not.toHaveBeenCalled();
    reloaded.unmount();
  });

  it.each([
    { saved: null, case: "a fresh browser" },
    { saved: "screen", case: "a saved value it does not know" },
  ])("shares a tab or screen by default, for $case, at Start and on Resume after a reload", async ({ saved }) => {
    if (saved) {
      window.localStorage.setItem("nojoin.capture.settings", JSON.stringify({ startMode: saved, version: 2 }));
    }
    useBrowser(DESKTOP_CHROME);
    const tab = openTab();
    expect(screen.getByRole("combobox", { name: "Recording source" })).toHaveValue("shared_audio");

    await startFromTheButton();
    expect(media.getDisplayMedia).toHaveBeenCalledOnce();
    expect(media.getUserMedia).toHaveBeenCalledOnce();

    const reloaded = await pauseAndReload(tab);
    await resumeThePausedRecording();
    expect(media.getDisplayMedia).toHaveBeenCalledTimes(2);
    reloaded.unmount();
  });

  it("keeps a phone on its microphone whatever a desktop saved", async () => {
    window.localStorage.setItem(
      "nojoin.capture.settings",
      JSON.stringify({ startMode: "shared_audio", version: 2 }),
    );
    useBrowser(ANDROID_CHROME);
    const tab = openTab();
    expect(screen.queryByRole("combobox", { name: "Recording source" })).not.toBeInTheDocument();

    await startFromTheButton();
    expect(media.getUserMedia).toHaveBeenCalledOnce();
    expect(media.getDisplayMedia).not.toHaveBeenCalled();
    tab.unmount();
  });
});
