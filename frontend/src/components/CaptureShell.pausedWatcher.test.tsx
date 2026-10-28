import { AxiosError } from "axios";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CaptureProvider, useCapture } from "@/lib/capture/CaptureProvider";
import { CaptureController } from "@/lib/capture/controller";
import { clearPausedCaptureContext, writePausedCaptureContext } from "@/lib/capture/shared";
import { useConnectivityStore } from "@/lib/connectivity/monitor";
import { useImportAudioStore } from "@/lib/importAudioStore";
import { useNotificationStore } from "@/lib/notificationStore";
import { ClientStatus, RecordingStatus, type Recording } from "@/types";

import CaptureShell from "./CaptureShell";
import MeetingControls from "./MeetingControls";
import ImportAudioButton from "./importAudio/ImportAudioButton";

const api = vi.hoisted(() => ({
  getUnfinishedCaptures: vi.fn(),
  initRecording: vi.fn(),
  discardRecordingCapture: vi.fn(),
  resumeRecordingCapture: vi.fn(),
  finalizeRecordingCapture: vi.fn(),
}));
const pickCaptureSource = vi.hoisted(() => vi.fn());
const routerPush = vi.hoisted(() => vi.fn());

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
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: routerPush }),
  usePathname: () => "/recordings",
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock("./SessionSignInDialog", () => ({ default: () => null }));

const paused = {
  id: "rec-paused",
  name: "Weekly sync",
  status: RecordingStatus.PAUSED,
  client_status: ClientStatus.PAUSED,
} as Recording;
const conflictMessage = "Handle the existing active recording before starting a new one.";
const controllers: CaptureController[] = [];
let held: LockInfo[];
let pending: LockInfo[];

function controller() {
  const capture = new CaptureController();
  controllers.push(capture);
  return capture;
}

function retainPausedContext(recordingId = paused.id) {
  writePausedCaptureContext({ recordingId, lastSequence: 0, persistedAt: Date.now() });
}

function WatcherState() {
  const { pausedRecording, runtimeActive, refreshPausedRecording } = useCapture();
  return <>
    <output data-testid="watcher-state">
      {runtimeActive ? "capturing" : "watching"}:{pausedRecording?.id ?? "none"}
    </output>
    <button onClick={() => void refreshPausedRecording()}>Check unfinished captures</button>
  </>;
}

beforeEach(() => {
  vi.resetAllMocks();
  clearPausedCaptureContext();
  held = [{ name: "nojoin-capture:rec-paused", mode: "shared" }];
  pending = [];
  // Exercise the real captureLock helper at the browser API boundary.
  vi.stubGlobal("navigator", {
    locks: { query: async () => ({ held, pending }) },
  });
  api.getUnfinishedCaptures.mockResolvedValue([paused]);
  useConnectivityStore.setState({ status: "online" });
  useNotificationStore.setState({ activeNotifications: [], history: [] });
  useImportAudioStore.setState({ isImportAudioOpen: false });
});

afterEach(async () => {
  cleanup();
  await Promise.all(controllers.splice(0).map((capture) => capture.destroy()));
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  clearPausedCaptureContext();
});

describe("paused capture ownership", () => {
  it.each([false, true])("does not offer a held paused capture, stalled=%s", async (capture_stalled) => {
    api.getUnfinishedCaptures.mockResolvedValue([{ ...paused, capture_stalled }]);
    const capture = controller();

    await expect(capture.refreshPausedRecording()).resolves.toBeNull();

    expect(capture.getState().pausedRecording).toBeNull();
  });

  it("offers a lock-free paused capture with retained context and withdraws it when another tab takes it", async () => {
    retainPausedContext();
    held = [{ name: "nojoin-capture:unrelated", mode: "shared" }];
    const capture = controller();

    await expect(capture.refreshPausedRecording()).resolves.toEqual(paused);
    expect(capture.getState().pausedRecording).toEqual(paused);

    held.push({ name: "nojoin-capture:rec-paused", mode: "shared" });
    await expect(capture.refreshPausedRecording()).resolves.toBeNull();
    expect(capture.getState().pausedRecording).toBeNull();
  });

  it("offers a locally remembered paused capture on the scheduled check after its lock is released", async () => {
    retainPausedContext();
    vi.useFakeTimers();
    const capture = controller();
    await capture.refreshPausedRecording();
    expect(capture.getState().pausedRecording).toBeNull();

    held = [];
    await vi.advanceTimersByTimeAsync(30_000);

    expect(api.getUnfinishedCaptures).toHaveBeenCalledTimes(2);
    expect(capture.getState().pausedRecording).toEqual(paused);
  });

  it("does not offer a paused capture while its owner's lock request is pending", async () => {
    pending = held;
    held = [];
    const capture = controller();

    await expect(capture.refreshPausedRecording()).resolves.toBeNull();
    expect(capture.getState().pausedRecording).toBeNull();
  });

  it.each([false, true])("keeps a remote pause quiet across polls, unrelated context=%s", async (unrelated) => {
    vi.useFakeTimers();
    held = [];
    if (unrelated) retainPausedContext("rec-unrelated");
    const capture = controller();
    await expect(capture.refreshPausedRecording()).resolves.toBeNull();
    await vi.advanceTimersByTimeAsync(60_000);
    expect(api.getUnfinishedCaptures).toHaveBeenCalledTimes(3);
    expect(capture.getState().pausedRecording).toBeNull();
  });

  it("keeps an explicit remote offer across polls without offering a later unrelated pause", async () => {
    vi.useFakeTimers();
    held = [];
    const capture = controller();
    await capture.refreshPausedRecording();
    await expect(capture.refreshPausedRecording({ onDemand: true })).resolves.toEqual(paused);
    await vi.advanceTimersByTimeAsync(30_000);
    expect(capture.getState().pausedRecording).toEqual(paused);

    api.getUnfinishedCaptures.mockResolvedValue([{ ...paused, id: "rec-next" }]);
    await expect(capture.refreshPausedRecording()).resolves.toBeNull();
    api.getUnfinishedCaptures.mockResolvedValue([paused]);
    await expect(capture.refreshPausedRecording()).resolves.toBeNull();
  });

  it("does not let an older automatic check erase an explicit offer", async () => {
    held = [];
    const capture = controller();
    let answerOldCheck!: (recordings: Recording[]) => void;
    api.getUnfinishedCaptures.mockImplementationOnce(() => new Promise<Recording[]>((resolve) => {
      answerOldCheck = resolve;
    }));
    const oldCheck = capture.refreshPausedRecording();
    await capture.refreshPausedRecording({ onDemand: true });
    answerOldCheck([]);
    await oldCheck;
    expect(capture.getState().pausedRecording).toEqual(paused);
  });

  it.each(["held", "pending"])("never explicitly offers a same-browser %s capture", async (kind) => {
    if (kind === "pending") { pending = held; held = []; }
    const capture = controller();
    await expect(capture.refreshPausedRecording({ onDemand: true })).resolves.toBeNull();
  });
});

describe("CaptureShell watching a paused capture", () => {
  it("keeps the dialog shut while another tab holds the lock, then offers recovery after release", async () => {
    // Only this tab's retained context justifies an automatic offer after release.
    retainPausedContext();
    render(<CaptureProvider><CaptureShell><WatcherState /></CaptureShell></CaptureProvider>);

    // Finish the real provider and guard's initial checks before asserting absence.
    await act(async () => {});
    expect(api.getUnfinishedCaptures).toHaveBeenCalled();
    expect(screen.getByTestId("watcher-state")).toHaveTextContent("watching:none");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Stop and process" })).not.toBeInTheDocument();
    expect(api.discardRecordingCapture).not.toHaveBeenCalled();

    held = [];
    fireEvent.click(screen.getByRole("button", { name: "Check unfinished captures" }));
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    expect(screen.getByText("Recording paused")).toBeInTheDocument();
  });

  it("offers recovery automatically after reloading with this tab's retained context", async () => {
    retainPausedContext();
    held = [];
    const oldPage = controller();
    await oldPage.destroy();
    render(<CaptureProvider><CaptureShell><WatcherState /></CaptureShell></CaptureProvider>);

    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    expect(screen.getByText("Recording paused")).toBeInTheDocument();
    expect(screen.getByTestId("watcher-state")).toHaveTextContent(`watching:${paused.id}`);
    expect(api.discardRecordingCapture).not.toHaveBeenCalled();
  });

  it("still offers stalled UPLOADING automatically without local context", async () => {
    held = [];
    api.getUnfinishedCaptures.mockResolvedValue([{
      ...paused, status: RecordingStatus.UPLOADING,
      client_status: ClientStatus.RECORDING, capture_stalled: true,
    }]);
    render(<CaptureProvider><CaptureShell><WatcherState /></CaptureShell></CaptureProvider>);

    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    expect(screen.getByText("Recording interrupted")).toBeInTheDocument();
    expect(screen.getByText(/received no audio.*over two minutes/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Stop and process" })).toBeEnabled();
    expect(api.discardRecordingCapture).not.toHaveBeenCalled();
  });

  it("explains and refuses Start Meeting before opening any media picker in a watcher", async () => {
    const conflict = new AxiosError("Request failed with status code 409");
    conflict.response = {
      status: 409,
      data: { detail: {
        code: "active_recording_exists", message: conflictMessage,
        recording_id: paused.id, status: paused.status,
      } },
    } as NonNullable<AxiosError["response"]>;
    api.initRecording.mockRejectedValue(conflict);
    vi.spyOn(console, "error").mockImplementation(() => {});
    render(<CaptureProvider><CaptureShell><MeetingControls variant="dashboard" /></CaptureShell></CaptureProvider>);
    await act(async () => {});

    const start = screen.getByRole("button", { name: "Start Meeting" });
    expect(start).toBeEnabled();
    fireEvent.click(start);

    await waitFor(() => expect(useNotificationStore.getState().activeNotifications).toEqual([
      expect.objectContaining({ type: "error", message: conflictMessage }),
    ]));
    expect(api.initRecording).toHaveBeenCalledOnce();
    expect(pickCaptureSource).not.toHaveBeenCalled();
    expect(routerPush).not.toHaveBeenCalled();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Start Meeting" })).toBeEnabled();
  });
});

describe("recovery on demand for another browser or device", () => {
  it("keeps imports available while another same-browser tab holds the capture", async () => {
    render(<CaptureProvider><CaptureShell><ImportAudioButton /></CaptureShell></CaptureProvider>);
    await act(async () => {});
    fireEvent.click(screen.getByRole("button", { name: "Import Audio" }));

    await waitFor(() => expect(useImportAudioStore.getState().isImportAudioOpen).toBe(true));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(api.discardRecordingCapture).not.toHaveBeenCalled();
  });

  it("reports a failed import recovery check and lets the next attempt retry", async () => {
    held = [];
    render(<CaptureProvider><CaptureShell><ImportAudioButton /></CaptureShell></CaptureProvider>);
    await act(async () => {});
    api.getUnfinishedCaptures.mockRejectedValueOnce(new Error("Connection lost"));
    fireEvent.click(screen.getByRole("button", { name: "Import Audio" }));

    await waitFor(() => expect(useNotificationStore.getState().activeNotifications).toEqual([
      expect.objectContaining({ type: "error", message: "Connection lost" }),
    ]));
    expect(useImportAudioStore.getState().isImportAudioOpen).toBe(false);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Import Audio" }));
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    expect(screen.getByText("Recording paused")).toBeInTheDocument();
  });

  it.each(["dashboard", "sidebar", "button", "compact", "icon"] as const)(
    "%s opens the paused decision only on demand", async (entry) => {
      held = [];
      // Complete real provider/guard selection through a controlled API boundary.
      let answerChecks!: (recordings: Recording[]) => void;
      const initialChecks = new Promise<Recording[]>((resolve) => { answerChecks = resolve; });
      api.getUnfinishedCaptures.mockReturnValue(initialChecks);
      render(<CaptureProvider><CaptureShell><WatcherState />{
        entry === "dashboard" || entry === "sidebar"
          ? <MeetingControls variant={entry} />
          : <ImportAudioButton appearance={entry} />
      }</CaptureShell></CaptureProvider>);
      await act(async () => { answerChecks([paused]); await initialChecks; });
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
      expect(screen.getByTestId("watcher-state")).toHaveTextContent("watching:none");

      const label = entry === "dashboard" || entry === "sidebar" ? "Start Meeting" : "Import Audio";
      const opener = screen.getByRole("button", { name: label });
      expect(opener).toBeEnabled();
      fireEvent.click(opener);

      expect(await screen.findByRole("dialog")).toBeInTheDocument();
      expect(screen.getByText("Recording paused")).toBeInTheDocument();
      expect(screen.getByText(/still.*open on another device.*finish it there/)).toBeInTheDocument();
      expect(screen.getByText(paused.name)).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Stop and process" })).toBeEnabled();
      expect(useImportAudioStore.getState().isImportAudioOpen).toBe(false);
      expect(api.initRecording).not.toHaveBeenCalled();
      expect(api.discardRecordingCapture).not.toHaveBeenCalled();
      expect(api.resumeRecordingCapture).not.toHaveBeenCalled();
      expect(api.finalizeRecordingCapture).not.toHaveBeenCalled();
      expect(pickCaptureSource).not.toHaveBeenCalled();
      expect(routerPush).not.toHaveBeenCalled();
      expect(useNotificationStore.getState().activeNotifications).toEqual([]);
    },
  );
});
