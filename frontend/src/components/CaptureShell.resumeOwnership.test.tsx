import type { ComponentPropsWithoutRef } from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { AxiosError } from "axios";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CaptureProvider } from "@/lib/capture/CaptureProvider";
import { audioTrack, mediaStream } from "@/lib/capture/releaseMicrophone.fixtures";
import { clearPausedCaptureContext } from "@/lib/capture/shared";
import { createSegmentUploader } from "@/lib/capture/uploader";
import { PickSourceError } from "@/lib/capture/pickSource";
import { useNotificationStore } from "@/lib/notificationStore";
import { ClientStatus, RecordingStatus, type Recording } from "@/types";

import CaptureShell from "./CaptureShell";
import LiveMeetingControls from "./LiveMeetingControls";
import MeetingControls from "./MeetingControls";
import ProcessingCard from "./dashboardRecordings/ProcessingCard";
import { DashboardRecordingCard } from "./upcomingMeetings/CalendarCards";

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
const browser = vi.hoisted(() => ({ realDetection: false }));
vi.mock("@/lib/capture/featureDetect", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/capture/featureDetect")>();
  return {
    ...actual,
    detectCaptureSupport: () => browser.realDetection
      ? actual.detectCaptureSupport()
      : { supported: true, mode: "microphone_only" },
  };
});
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
vi.mock("next/link", () => ({
  default: ({ children, ...props }: ComponentPropsWithoutRef<"a">) => <a {...props}>{children}</a>,
}));

const paused = {
  id: "rec-1",
  name: "Weekly sync",
  status: RecordingStatus.PAUSED,
  client_status: ClientStatus.PAUSED,
} as Recording;
const lifecycle = (status: string) => ({
  recording_id: paused.id, status, last_sequence: 4, capture_epoch: 4,
});

beforeEach(() => {
  vi.clearAllMocks();
  browser.realDetection = false;
  useNotificationStore.setState({ activeNotifications: [], history: [] });
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
    const liveCopy = { ...paused, status: RecordingStatus.UPLOADING, client_status: ClientStatus.RECORDING };
    render(<CaptureProvider><CaptureShell>
      <LiveMeetingControls size="bar" serverPaused />
      <ProcessingCard recordings={[liveCopy]} />
      <DashboardRecordingCard
        recording={{
          id: liveCopy.id, name: liveCopy.name, starts_at: "2026-10-29T09:00:00Z",
          status: liveCopy.status, client_status: liveCopy.client_status, speaker_names: [], tags: [],
        }}
        timeZone="UTC"
        showDate
      />
    </CaptureShell></CaptureProvider>);
    await act(async () => {});
    expect(screen.getByTitle("Stop recording")).toBeDisabled();

    fireEvent.click(await screen.findByRole("button", { name: /Resume recording/ }));

    await waitFor(() => expect(screen.getByTitle("Pause recording")).toBeEnabled());
    expect(api.resumeRecordingCapture).toHaveBeenCalledWith(paused.id, { epoch: undefined });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(screen.getByTitle("Stop recording")).toBeEnabled();
    expect(screen.getByTitle("Discard recording")).toBeEnabled();
    // The controls' pill, the dashboard badge and the agenda card all say
    // this tab records.
    expect(screen.getAllByText("Recording")).toHaveLength(3);
    expect(screen.queryByText("Live")).not.toBeInTheDocument();

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

const errors = () =>
  useNotificationStore.getState().activeNotifications.filter(({ type }) => type === "error");

describe("the recovery dialog in a browser that cannot capture", () => {
  it("offers Stop and Discard but not Resume, and says why once", async () => {
    browser.realDetection = true;
    vi.stubGlobal("navigator", {
      userAgent: "Mozilla/5.0 (X11; Linux x86_64; rv:140.0) Gecko/20100101 Firefox/140.0",
      mediaDevices: { getUserMedia: vi.fn(), getDisplayMedia: vi.fn() },
      locks: { query: async () => ({ held: [], pending: [] }) },
    });
    render(<CaptureProvider><CaptureShell><div /></CaptureShell></CaptureProvider>);

    const resume = await screen.findByRole("button", { name: "Resume recording" });
    expect(resume).toBeDisabled();
    expect(screen.getAllByText(/not available in Firefox/)).toHaveLength(1);
    expect(screen.getByRole("button", { name: "Discard recording" })).toBeEnabled();
    fireEvent.click(resume);
    fireEvent.click(screen.getByRole("button", { name: "Stop and process" }));

    await waitFor(() => expect(api.finalizeRecordingCapture).toHaveBeenCalledOnce());
    expect(api.finalizeRecordingCapture.mock.calls[0][0]).toBe(paused.id);
    expect(api.resumeRecordingCapture).not.toHaveBeenCalled();
    expect(errors()).toEqual([]);
  });
});

describe("a capture the browser refuses", () => {
  const refused = new Error("Permission to use the microphone was denied.");

  it("reports a refused Resume once", async () => {
    pickCaptureSource.mockRejectedValue(refused);
    vi.spyOn(console, "error").mockImplementation(() => {});
    render(<CaptureProvider><CaptureShell><div /></CaptureShell></CaptureProvider>);

    fireEvent.click(await screen.findByRole("button", { name: "Resume recording" }));

    await waitFor(() => expect(errors()).not.toEqual([]));
    await act(async () => {});
    expect(errors()).toHaveLength(1);
  });

  it("reports a refused Start once", async () => {
    pathname = "/";
    api.getUnfinishedCaptures.mockResolvedValue([]);
    pickCaptureSource.mockRejectedValue(refused);
    vi.spyOn(console, "error").mockImplementation(() => {});
    render(<CaptureProvider><CaptureShell><MeetingControls variant="dashboard" /></CaptureShell></CaptureProvider>);
    await act(async () => {});

    fireEvent.click(screen.getByRole("button", { name: "Start Meeting" }));

    await waitFor(() => expect(errors()).not.toEqual([]));
    await act(async () => {});
    expect(errors()).toHaveLength(1);
  });
});

describe("a capture whose tab is reloaded while it records", () => {
  // The server never heard the old page's pause, and the capture is not stalled.
  const stillUploading = {
    ...paused, status: RecordingStatus.UPLOADING, client_status: ClientStatus.RECORDING, capture_stalled: false,
    capture_epoch: 3,
  } as Recording;
  const lockQuery = vi.fn(async () => ({ held: [] as LockInfo[], pending: [] as LockInfo[] }));

  /**
   * Starts a capture at epoch 3, then reloads the page while it records. With
   * `uploaded: false` the tab was offline, so nothing reached the server.
   */
  const recordThenReload = async ({ uploaded = true } = {}) => {
    vi.stubGlobal("navigator", {
      locks: { query: lockQuery, request: (_name: string, _options: unknown, hold: () => Promise<void>) => hold() },
      // The beacon is queued as the page unloads but never reaches the server.
      sendBeacon: vi.fn(() => true),
    });
    pathname = "/";
    api.getUnfinishedCaptures.mockResolvedValue([]);
    const oldPage = render(
      <CaptureProvider><CaptureShell><MeetingControls variant="dashboard" /></CaptureShell></CaptureProvider>,
    );
    await act(async () => {});
    fireEvent.click(screen.getByRole("button", { name: "Start Meeting" }));
    await screen.findByTitle("Pause recording");
    // The server has the first segment, so there is a meeting to go back to.
    if (uploaded) act(() => vi.mocked(createSegmentUploader).mock.calls.at(-1)?.[0].onUploaded?.(0));
    await act(async () => { window.dispatchEvent(new Event("pagehide")); });
    oldPage.unmount();

    api.getUnfinishedCaptures.mockResolvedValue([stillUploading]);
    // The server takes over a capture still uploading only for the epoch its
    // tab held, and refuses anyone else until two minutes have passed.
    api.resumeRecordingCapture.mockImplementation(async (_id: string, options: { reclaimEpoch?: number }) => {
      if (options.reclaimEpoch !== 3) {
        throw new Error("This recording is still in progress and received audio in the last two minutes.");
      }
      return { recording_id: paused.id, status: "UPLOADING", last_sequence: 0, capture_epoch: 4 };
    });
    pathname = `/recordings/${paused.id}`;
  };

  beforeEach(() => {
    lockQuery.mockResolvedValue({ held: [], pending: [] });
  });

  it("is offered again in that tab and resumes at once", async () => {
    await recordThenReload();
    render(<CaptureProvider><CaptureShell><LiveMeetingControls size="bar" /></CaptureShell></CaptureProvider>);

    fireEvent.click(await screen.findByRole("button", { name: /Resume recording/ }));

    await waitFor(() => expect(screen.getByTitle("Pause recording")).toBeEnabled());
    expect(errors()).toEqual([]);
  });

  it("is not offered while another tab of this browser holds it", async () => {
    // A duplicate of the reloaded tab carries the same per-tab storage, so
    // only the lock tells it the other copy has already taken the capture.
    await recordThenReload();
    lockQuery.mockResolvedValue({ held: [{ name: `nojoin-capture:${paused.id}`, mode: "shared" }], pending: [] });
    render(<CaptureProvider><CaptureShell><LiveMeetingControls size="bar" /></CaptureShell></CaptureProvider>);
    await act(async () => {});
    await act(async () => {});

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(api.resumeRecordingCapture).not.toHaveBeenCalled();
  });

  it("is neither offered nor discarded once another device has taken it over", async () => {
    // Reloaded offline before the first upload, then resumed and recording on
    // another device, which has no lock in this browser.
    await recordThenReload({ uploaded: false });
    api.getUnfinishedCaptures.mockResolvedValue([{ ...stillUploading, capture_epoch: 4 }]);
    render(<CaptureProvider><CaptureShell><LiveMeetingControls size="bar" /></CaptureShell></CaptureProvider>);
    await act(async () => {});
    await act(async () => {});

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(api.discardRecordingCapture).not.toHaveBeenCalled();
  });

  it("is not deleted by this tab once another device has taken it over and paused it", async () => {
    await recordThenReload({ uploaded: false });
    api.getUnfinishedCaptures.mockResolvedValue([{ ...paused, capture_epoch: 4 }]);
    // The server refuses a discard sent with an epoch it has moved past.
    let deleted = false;
    api.discardRecordingCapture.mockImplementation(
      async (_id: string, _reason: unknown, options: { epoch?: number } = {}) => {
        if (options.epoch !== undefined && options.epoch !== 4) {
          throw Object.assign(new AxiosError("Request failed with status code 409"), {
            response: { status: 409, data: { detail: { code: "capture_superseded", message: "Continued elsewhere." } } },
          });
        }
        deleted = true;
      },
    );
    render(<CaptureProvider><CaptureShell><LiveMeetingControls size="bar" /></CaptureShell></CaptureProvider>);

    await waitFor(() => expect(api.discardRecordingCapture).toHaveBeenCalled());
    await act(async () => {});
    expect(deleted).toBe(false);
  });
});

describe("Start Meeting", () => {
  it("opens the share picker after a single request, with no recovery check first", async () => {
    // The picker needs the click's activation, which every round trip before
    // it uses up; a recovery check waits for the server's refusal instead.
    pathname = "/";
    api.getUnfinishedCaptures.mockResolvedValue([]);
    render(<CaptureProvider><CaptureShell><MeetingControls variant="dashboard" /></CaptureShell></CaptureProvider>);
    await act(async () => {});
    const checksBeforeStart = api.getUnfinishedCaptures.mock.calls.length;
    let checksBeforePicker = -1;
    const pickSources = pickCaptureSource.getMockImplementation()!;
    pickCaptureSource.mockImplementationOnce((...args: unknown[]) => {
      checksBeforePicker = api.getUnfinishedCaptures.mock.calls.length;
      return pickSources(...args);
    });

    fireEvent.click(screen.getByRole("button", { name: "Start Meeting" }));

    await screen.findByTitle("Pause recording");
    expect(api.initRecording).toHaveBeenCalledOnce();
    expect(checksBeforePicker).toBe(checksBeforeStart);
  });
});

describe("Start Meeting whose share picker is cancelled late", () => {
  it("does not delete the recording another device took over meanwhile", async () => {
    pathname = "/";
    api.getUnfinishedCaptures.mockResolvedValue([]);
    // The picker stayed open past the stall threshold, and another device
    // resumed the reservation (epoch 4) before the user cancelled it here.
    pickCaptureSource.mockRejectedValue(new PickSourceError("display_cancelled", "Picker cancelled"));
    let deleted = false;
    api.discardRecordingCapture.mockImplementation(
      async (_id: string, _reason: unknown, options: { epoch?: number } = {}) => {
        if (options.epoch !== undefined && options.epoch !== 4) {
          throw Object.assign(new AxiosError("Request failed with status code 409"), {
            response: { status: 409, data: { detail: { code: "capture_superseded", message: "Continued elsewhere." } } },
          });
        }
        deleted = true;
      },
    );
    render(<CaptureProvider><CaptureShell><MeetingControls variant="dashboard" /></CaptureShell></CaptureProvider>);
    await act(async () => {});

    fireEvent.click(screen.getByRole("button", { name: "Start Meeting" }));

    await waitFor(() => expect(api.discardRecordingCapture).toHaveBeenCalled());
    await act(async () => {});
    expect(deleted).toBe(false);
  });
});

describe("Start Meeting after a failed stop", () => {
  it("says why it does not start while this tab still holds the capture", async () => {
    pathname = "/";
    api.getUnfinishedCaptures.mockResolvedValue([]);
    vi.spyOn(console, "error").mockImplementation(() => {});
    render(<CaptureProvider><CaptureShell><MeetingControls variant="dashboard" /></CaptureShell></CaptureProvider>);
    await act(async () => {});
    fireEvent.click(screen.getByRole("button", { name: "Start Meeting" }));
    await screen.findByTitle("Pause recording");
    // The stop fails, and the pause owed after it has not reached the server,
    // which still lists the capture as uploading.
    api.getUnfinishedCaptures.mockResolvedValue([
      { ...paused, status: RecordingStatus.UPLOADING, client_status: ClientStatus.RECORDING },
    ]);
    api.finalizeRecordingCapture.mockRejectedValue(new Error("Internal Server Error"));
    api.pauseRecordingCapture.mockReturnValue(new Promise(() => {}));

    fireEvent.click(screen.getByTitle("Stop recording"));
    const start = await screen.findByRole("button", { name: "Start Meeting" });
    expect(start).toBeEnabled();
    fireEvent.click(start);

    await waitFor(() => expect(errors()).toContainEqual(expect.objectContaining({
      message: "The last recording was not stopped or discarded. Stop or discard it from its page before starting a new one.",
    })));
    expect(api.initRecording).toHaveBeenCalledOnce();
  });
});
