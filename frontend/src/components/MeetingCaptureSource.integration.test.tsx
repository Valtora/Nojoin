import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useEffect } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CaptureProvider, useCapture } from "@/lib/capture/CaptureProvider";

import CaptureShell from "./CaptureShell";
import MeetingControls from "./MeetingControls";

// The real provider, controller, capture shell and its source dialog, feature
// detection, settings storage and source picker run here. Only the browser's
// media APIs, the network and the audio graph (Web Audio, covered by the
// mixer's and recorder's own tests) are stubbed.

const routerPush = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: routerPush }),
  usePathname: () => "/",
  useSearchParams: () => new URLSearchParams(),
}));

const api = vi.hoisted(() => ({
  initRecording: vi.fn(),
  discardRecordingCapture: vi.fn(),
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
vi.mock("./SessionSignInDialog", () => ({ default: () => null }));
vi.mock("./LiveMeetingControls", () => ({
  default: () => <div data-testid="live-controls" />,
  CaptureNotices: () => null,
}));

const DESKTOP_CHROME =
  "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36";
const ANDROID_CHROME =
  "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Mobile Safari/537.36";
const DESKTOP_FIREFOX =
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:131.0) Gecko/20100101 Firefox/131.0";
const DESKTOP_SAFARI =
  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15";

const track = (kind: "audio" | "video", settings: MediaTrackSettings = {}) =>
  Object.assign(new EventTarget(), {
    kind,
    label: kind,
    enabled: true,
    muted: false,
    readyState: "live",
    stop: vi.fn(),
    getSettings: () => settings,
  }) as unknown as MediaStreamTrack;

const stream = (tracks: MediaStreamTrack[]) =>
  ({
    getTracks: () => tracks,
    getAudioTracks: () => tracks.filter((t) => t.kind === "audio"),
    getVideoTracks: () => tracks.filter((t) => t.kind === "video"),
  }) as unknown as MediaStream;

const sharedTab = () => stream([track("audio"), track("video", { displaySurface: "browser" })]);
const cancelled = () => Object.assign(new Error("Permission denied"), { name: "NotAllowedError" });

const media = {
  getDisplayMedia: vi.fn(async () => sharedTab()),
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
      <CaptureShell>
        <MeetingControls />
        <Probe />
      </CaptureShell>
    </CaptureProvider>,
  );

const saveRecordingSource = (startMode: string) =>
  window.localStorage.setItem("nojoin.capture.settings", JSON.stringify({ startMode, version: 2 }));

const clickStart = () => fireEvent.click(screen.getByRole("button", { name: "Start Meeting" }));

/** The open source dialog, found by its title. */
const sourceDialog = (title: string) => screen.findByRole("dialog", { name: title });

const useBrowser = (userAgent: string) => {
  vi.spyOn(window.navigator, "userAgent", "get").mockReturnValue(userAgent);
};

const startFromTheButton = async () => {
  clickStart();
  await waitFor(() => expect(routerPush).toHaveBeenCalledWith("/recordings/rec-1"));
  // The probe publishes the state from an effect, after the navigation.
  await waitFor(() => expect(capture.status).toBe("recording"));
};

/**
 * Pauses and reloads the tab, then opens the recording, whose page offers the
 * paused recording (a passive check only badges it).
 */
const pauseAndReload = async (tab: ReturnType<typeof openTab>) => {
  await act(() => capture.pause());
  // Audio reached the server, so the reloaded shell offers the recording
  // rather than clearing it as an empty interruption.
  const key = "nojoin.capture.paused-recording";
  const context = JSON.parse(window.sessionStorage.getItem(key) ?? "{}");
  window.sessionStorage.setItem(key, JSON.stringify({ ...context, lastSequence: 2 }));
  api.getUnfinishedCaptures.mockResolvedValue([PAUSED]);
  tab.unmount();
  const reloaded = openTab();
  await act(async () => {
    await capture.refreshPausedRecording({ recordingId: "rec-1" });
  });
  await waitFor(() => expect(capture.pausedRecording?.id).toBe("rec-1"));
  return reloaded;
};

const resumeThePausedRecording = async () => {
  await act(async () => {
    await capture.resume();
  });
  await waitFor(() => expect(capture.status).toBe("recording"));
};

/** A Resume after a reload in a tab that does not know the meeting's source. */
const forgetTheSource = () => {
  const key = "nojoin.capture.paused-recording";
  const context = JSON.parse(window.sessionStorage.getItem(key) ?? "{}");
  delete context.mode;
  window.sessionStorage.setItem(key, JSON.stringify(context));
};

describe("Recording source and the capture dialog (issue #958)", () => {
  beforeEach(() => {
    routerPush.mockReset();
    media.getDisplayMedia.mockReset();
    media.getDisplayMedia.mockImplementation(async () => sharedTab());
    media.getUserMedia.mockClear();
    Object.defineProperty(window.navigator, "mediaDevices", { value: media, configurable: true });
    vi.stubGlobal("AudioWorkletNode", class {});
    api.initRecording.mockResolvedValue({ id: "rec-1", name: "Meeting", capture_epoch: 0 });
    api.discardRecordingCapture.mockResolvedValue(undefined);
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

  it("offers no source select beside Start", () => {
    useBrowser(DESKTOP_CHROME);
    openTab();
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
  });

  it("records only the microphone under Microphone only, and Resume keeps the meeting's source", async () => {
    saveRecordingSource("microphone_only");
    useBrowser(DESKTOP_CHROME);
    const tab = openTab();

    await startFromTheButton();
    expect(media.getUserMedia).toHaveBeenCalledOnce();
    expect(media.getDisplayMedia).not.toHaveBeenCalled();

    // Changing the setting mid-meeting does not change the meeting's source.
    act(() => capture.controller.updateSettings({ startMode: "shared_audio" }));
    const reloaded = await pauseAndReload(tab);
    await resumeThePausedRecording();
    expect(media.getUserMedia).toHaveBeenCalledTimes(2);
    expect(media.getDisplayMedia).not.toHaveBeenCalled();
    reloaded.unmount();
  });

  it.each([
    { saved: null, case: "a fresh browser" },
    { saved: "screen", case: "a saved value it does not know" },
  ])("shares system audio by default, for $case, at Start and on Resume after a reload", async ({ saved }) => {
    if (saved) saveRecordingSource(saved);
    useBrowser(DESKTOP_CHROME);
    const tab = openTab();

    await startFromTheButton();
    expect(media.getDisplayMedia).toHaveBeenCalledOnce();
    expect(media.getUserMedia).toHaveBeenCalledOnce();

    // The meeting shared system audio, so Resume shares again even under a
    // setting that would ask.
    act(() => capture.controller.updateSettings({ startMode: "ask" }));
    const reloaded = await pauseAndReload(tab);
    await resumeThePausedRecording();
    expect(media.getDisplayMedia).toHaveBeenCalledTimes(2);
    reloaded.unmount();
  });

  it("offers the microphone alone after Cancel in the picker", async () => {
    useBrowser(DESKTOP_CHROME);
    media.getDisplayMedia.mockRejectedValueOnce(cancelled());
    openTab();

    clickStart();
    const dialog = await sourceDialog("Record your microphone only?");
    expect(dialog).toHaveTextContent("can't record the meeting's system audio");
    expect(api.discardRecordingCapture).toHaveBeenCalledWith("rec-1", "display_picker_cancelled", { epoch: 0 });
    expect(media.getUserMedia).not.toHaveBeenCalled();
    const microphoneOnly = screen.getByRole("button", { name: "Record microphone only" });
    await waitFor(() => expect(microphoneOnly).toHaveFocus());
    expect(screen.getByRole("button", { name: "Share system audio" })).toBeInTheDocument();

    fireEvent.click(microphoneOnly);
    await waitFor(() => expect(routerPush).toHaveBeenCalledWith("/recordings/rec-1"));
    await waitFor(() => expect(capture.status).toBe("recording"));
    expect(media.getDisplayMedia).toHaveBeenCalledOnce();
    expect(media.getUserMedia).toHaveBeenCalledOnce();
    expect(api.initRecording).toHaveBeenCalledTimes(2);
  });

  it("closes on Cancel with nothing started", async () => {
    useBrowser(DESKTOP_CHROME);
    media.getDisplayMedia.mockRejectedValueOnce(cancelled());
    openTab();

    clickStart();
    await sourceDialog("Record your microphone only?");
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(capture.status).toBe("idle");
    expect(api.initRecording).toHaveBeenCalledOnce();
    expect(routerPush).not.toHaveBeenCalled();
  });

  it("says when the computer refused the share", async () => {
    useBrowser(DESKTOP_CHROME);
    media.getDisplayMedia.mockRejectedValueOnce(
      Object.assign(new Error("Permission denied by system"), { name: "NotAllowedError" }),
    );
    openTab();

    clickStart();
    const dialog = await sourceDialog("Your browser didn't share system audio");
    expect(dialog).toHaveTextContent("System Settings > Privacy & Security");
    fireEvent.click(screen.getByRole("button", { name: "Share system audio" }));
    await waitFor(() => expect(capture.status).toBe("recording"));
    expect(media.getDisplayMedia).toHaveBeenCalledTimes(2);
  });

  it.each([
    { surface: "browser", copy: "Also share tab audio" },
    { surface: "window", copy: "can't share the sound of a single window" },
    { surface: "monitor", copy: "Also share system audio" },
  ])("stops before recording a $surface share with no sound", async ({ surface, copy }) => {
    useBrowser(DESKTOP_CHROME);
    const silent = track("video", { displaySurface: surface } as MediaTrackSettings);
    media.getDisplayMedia.mockResolvedValueOnce(stream([silent]));
    openTab();

    clickStart();
    const dialog = await sourceDialog("Your share has no sound");
    expect(dialog).toHaveTextContent(copy);
    expect(silent.stop).toHaveBeenCalled();
    expect(media.getUserMedia).not.toHaveBeenCalled();
    expect(api.discardRecordingCapture).toHaveBeenCalledWith("rec-1", "display_audio_missing", { epoch: 0 });
    const shareAgain = screen.getByRole("button", { name: "Share again" });
    await waitFor(() => expect(shareAgain).toHaveFocus());
    expect(screen.getByRole("button", { name: "Record microphone only" })).toBeInTheDocument();

    fireEvent.click(shareAgain);
    await waitFor(() => expect(capture.status).toBe("recording"));
    expect(media.getDisplayMedia).toHaveBeenCalledTimes(2);
  });

  it("chooses before the picker under Ask each time, and Cancel there comes back to the chooser", async () => {
    saveRecordingSource("ask");
    useBrowser(DESKTOP_CHROME);
    openTab();

    clickStart();
    await sourceDialog("Choose what to record");
    expect(api.initRecording).not.toHaveBeenCalled();
    expect(media.getDisplayMedia).not.toHaveBeenCalled();
    const share = screen.getByRole("button", { name: "Microphone and system audio" });
    await waitFor(() => expect(share).toHaveFocus());

    media.getDisplayMedia.mockRejectedValueOnce(cancelled());
    fireEvent.click(share);
    await waitFor(() => expect(media.getDisplayMedia).toHaveBeenCalledOnce());
    await sourceDialog("Choose what to record");

    fireEvent.click(screen.getByRole("button", { name: "Microphone only" }));
    await waitFor(() => expect(capture.status).toBe("recording"));
    expect(media.getDisplayMedia).toHaveBeenCalledOnce();
    expect(media.getUserMedia).toHaveBeenCalledOnce();
  });

  it("asks again on Resume after a reload when the tab does not know the source, with no error", async () => {
    useBrowser(DESKTOP_CHROME);
    const tab = openTab();
    await startFromTheButton();
    const reloaded = await pauseAndReload(tab);
    forgetTheSource();
    reloaded.unmount();
    const fresh = openTab();
    await act(async () => {
      await capture.refreshPausedRecording({ recordingId: "rec-1" });
    });

    media.getDisplayMedia.mockRejectedValueOnce(cancelled());
    await act(async () => {
      await expect(capture.resume("rec-1")).resolves.toBeNull();
    });
    await sourceDialog("Resume with your microphone only?");
    // The recovery dialog gives way while the source question is open.
    await waitFor(() => expect(screen.getAllByRole("dialog")).toHaveLength(1));
    expect(capture.error).toBeNull();
    expect(api.resumeRecordingCapture).not.toHaveBeenCalled();
    expect(capture.status).not.toBe("error");

    fireEvent.click(screen.getByRole("button", { name: "Resume microphone only" }));
    await waitFor(() => expect(capture.status).toBe("recording"));
    expect(api.resumeRecordingCapture).toHaveBeenCalledOnce();
    expect(media.getDisplayMedia).toHaveBeenCalledTimes(2);
    expect(media.getUserMedia).toHaveBeenCalledTimes(2);
    fresh.unmount();
  });

  it("keeps a phone on its microphone whatever the setting", async () => {
    saveRecordingSource("ask");
    useBrowser(ANDROID_CHROME);
    const tab = openTab();

    await startFromTheButton();
    expect(media.getUserMedia).toHaveBeenCalledOnce();
    expect(media.getDisplayMedia).not.toHaveBeenCalled();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    tab.unmount();
  });

  // Both offer getDisplayMedia (stubbed here too), but their shares carry no audio.
  it.each([
    { name: "Firefox", userAgent: DESKTOP_FIREFOX },
    { name: "Safari", userAgent: DESKTOP_SAFARI },
  ])("records the microphone in desktop $name, at Start and on Resume after a reload", async ({ userAgent }) => {
    saveRecordingSource("ask");
    useBrowser(userAgent);
    const tab = openTab();

    await startFromTheButton();
    expect(media.getUserMedia).toHaveBeenCalledOnce();
    expect(media.getDisplayMedia).not.toHaveBeenCalled();

    const reloaded = await pauseAndReload(tab);
    await resumeThePausedRecording();
    expect(media.getUserMedia).toHaveBeenCalledTimes(2);
    expect(media.getDisplayMedia).not.toHaveBeenCalled();
    reloaded.unmount();
  });
});
