import { AxiosError } from "axios";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CaptureProvider, useCapture } from "@/lib/capture/CaptureProvider";
import { CaptureController } from "@/lib/capture/controller";
import { PickSourceError } from "@/lib/capture/pickSource";
import { clearPausedCaptureContext, writePausedCaptureContext } from "@/lib/capture/shared";
import { useConnectivityStore } from "@/lib/connectivity/monitor";
import { useImportAudioStore } from "@/lib/importAudioStore";
import { useNotificationStore } from "@/lib/notificationStore";
import { AudioClock } from "@/lib/notes/audioClock";
import { ClientStatus, RecordingStatus, type Recording } from "@/types";

import CaptureShell from "./CaptureShell";
import MeetingControls from "./MeetingControls";
import LiveMeetingControls from "./LiveMeetingControls";
import ImportAudioButton from "./importAudio/ImportAudioButton";
import RecordingAttentionBadge from "./RecordingAttentionBadge";
import Sidebar from "./Sidebar";

const api = vi.hoisted(() => ({
  getUnfinishedCaptures: vi.fn(),
  initRecording: vi.fn(),
  discardRecordingCapture: vi.fn(),
  resumeRecordingCapture: vi.fn(),
  finalizeRecordingCapture: vi.fn(),
  getRecordings: vi.fn(),
  getGlobalSpeakers: vi.fn(),
  getTags: vi.fn(),
  getRecordingsCalendar: vi.fn(),
}));
const pickCaptureSource = vi.hoisted(() => vi.fn());
const routerPush = vi.hoisted(() => vi.fn());
let pathname = "/recordings";
let captureMode: "microphone_only" | "shared_audio" = "microphone_only";

vi.mock("@/lib/api", async (importOriginal) => ({
  ...await importOriginal<typeof import("@/lib/api")>(),
  ...api,
}));
vi.mock("@/lib/capture/featureDetect", () => ({
  detectCaptureSupport: () => ({ supported: true, mode: captureMode }),
}));
vi.mock("@/lib/capture/pickSource", async (importOriginal) => ({
  ...await importOriginal<typeof import("@/lib/capture/pickSource")>(),
  pickCaptureSource,
}));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: routerPush }),
  usePathname: () => pathname,
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock("./SessionSignInDialog", () => ({ default: () => null }));
vi.mock("./MonthCalendar", () => ({ default: () => null }));
vi.mock("./ViewportDensityProvider", () => ({
  useViewportDensity: () => ({ isCompact: false, isDesktop: true }),
}));
vi.mock("@/lib/useUserTimeZone", () => ({ useUserTimeZone: () => "UTC" }));
vi.mock("@/lib/notes/useNoteOutbox", () => ({ useUnsentRecordings: () => [] }));
vi.mock("@/lib/notes/deletedRecordingNotes", () => ({ useForgetNotesOfDeletedRecordings: () => {} }));

const paused = {
  id: "rec-paused",
  name: "Weekly sync",
  status: RecordingStatus.PAUSED,
  client_status: ClientStatus.PAUSED,
} as Recording;
const conflictMessage = "Handle the existing active recording before starting a new one.";
const inAnotherTabMessage = "A recording is open in another tab. Stop or discard it there before starting a new one.";

/** The server refusing a new recording while `recording` is unfinished. */
function activeRecordingConflict(recording: Recording, message = conflictMessage) {
  const conflict = new AxiosError("Request failed with status code 409");
  conflict.response = {
    status: 409, data: { detail: {
      code: "active_recording_exists", message,
      recording_id: recording.id, status: recording.status,
    } },
  } as NonNullable<AxiosError["response"]>;
  return conflict;
}

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
  const { attentionRecordings, pausedRecording, runtimeActive, refreshPausedRecording } = useCapture();
  return <>
    <output data-testid="watcher-state">
      {runtimeActive ? "capturing" : "watching"}:{pausedRecording?.id ?? "none"}
    </output>
    <output data-testid="attention-state">{attentionRecordings.map(({ id }) => id).join(",")}</output>
    <button onClick={() => void refreshPausedRecording()}>Check unfinished captures</button>
  </>;
}

beforeEach(() => {
  vi.resetAllMocks();
  clearPausedCaptureContext();
  pathname = "/recordings";
  captureMode = "microphone_only";
  held = [{ name: "nojoin-capture:rec-paused", mode: "shared" }];
  pending = [];
  // Exercise the real captureLock helper at the browser API boundary.
  vi.stubGlobal("navigator", {
    locks: { query: async () => ({ held, pending }) },
  });
  api.getUnfinishedCaptures.mockResolvedValue([paused]);
  api.getRecordings.mockResolvedValue([]);
  api.getGlobalSpeakers.mockResolvedValue([]);
  api.getTags.mockResolvedValue([]);
  api.getRecordingsCalendar.mockResolvedValue({ day_counts: [] });
  useConnectivityStore.setState({ status: "online" });
  useNotificationStore.setState({ activeNotifications: [], history: [] });
  useImportAudioStore.setState({ isImportAudioOpen: false });
});

const interrupted = {
  ...paused, status: RecordingStatus.UPLOADING,
  client_status: ClientStatus.RECORDING, capture_stalled: true,
};

function watcherPage() {
  return <CaptureProvider><CaptureShell><WatcherState /></CaptureShell></CaptureProvider>;
}

describe("recovery when entering a recording page", () => {
  beforeEach(() => { held = []; });

  it("discovers a later recording through the sidebar's existing refresh without opening recovery", async () => {
    vi.useFakeTimers();
    api.getUnfinishedCaptures.mockResolvedValue([]);
    render(<CaptureProvider><CaptureShell><Sidebar /><WatcherState /></CaptureShell></CaptureProvider>);
    await act(async () => {});
    expect(screen.getByTestId("attention-state")).toBeEmptyDOMElement();
    const initialFetches = api.getRecordings.mock.calls.length;

    // A starts after this watcher opens; its pause remains held until A closes.
    api.getRecordings.mockResolvedValue([paused]);
    api.getUnfinishedCaptures.mockResolvedValue([paused]);
    held = [{ name: `nojoin-capture:${paused.id}`, mode: "shared" }];
    await act(async () => { await vi.advanceTimersByTimeAsync(15_000); });
    expect(screen.getByTestId("attention-state")).toBeEmptyDOMElement();
    expect(screen.queryByText("Paused, needs attention")).not.toBeInTheDocument();

    held = [];
    await act(async () => { await vi.advanceTimersByTimeAsync(15_000); });

    expect(screen.getByTestId("attention-state")).toHaveTextContent(paused.id);
    expect(screen.getByText("Paused, needs attention")).toBeVisible();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(api.getRecordings).toHaveBeenCalledTimes(initialFetches + 2);
    await act(async () => { await vi.advanceTimersByTimeAsync(15_000); });
    expect(api.getRecordings).toHaveBeenCalledTimes(initialFetches + 3);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it.each([
    [paused, "shared_audio"], [paused, "microphone_only"],
    [interrupted, "shared_audio"], [interrupted, "microphone_only"],
  ] as const)("opens a fresh %s page in %s mode without retained context", async (recording, mode) => {
    pathname = `/recordings/${recording.id}`;
    captureMode = mode;
    api.getUnfinishedCaptures.mockResolvedValue([recording]);
    render(watcherPage());

    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    expect(screen.getByText(recording.name)).toBeInTheDocument();
    expect(screen.getByText(recording.status === RecordingStatus.PAUSED
      ? "Recording paused" : "Recording interrupted")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Stop and process" })).toBeEnabled();
    expect(pickCaptureSource).not.toHaveBeenCalled();
  });

  it.each([paused, interrupted])("opens $status on navigation to its page, with the provider preserved", async (recording) => {
    api.getUnfinishedCaptures.mockResolvedValue([recording]);
    const { rerender } = render(watcherPage());
    await waitFor(() => expect(screen.getByTestId("attention-state")).toHaveTextContent(recording.id));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    pathname = "/recordings/another-meeting";
    rerender(watcherPage());
    await act(async () => {});
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    pathname = `/recordings/${recording.id}`;
    rerender(watcherPage());
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    expect(screen.getByText(recording.name)).toBeInTheDocument();
  });

  it.each([paused, interrupted])("does not open when a $status condition arrives on the page already open", async (recording) => {
    pathname = `/recordings/${recording.id}`;
    api.getUnfinishedCaptures.mockResolvedValue([]);
    const { rerender } = render(watcherPage());
    await act(async () => {});
    expect(api.getUnfinishedCaptures).toHaveBeenCalled();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    api.getUnfinishedCaptures.mockResolvedValue([recording]);
    fireEvent.click(screen.getByRole("button", { name: "Check unfinished captures" }));
    await waitFor(() => expect(screen.getByTestId("attention-state")).toHaveTextContent(recording.id));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    pathname = "/recordings";
    rerender(watcherPage());
    await act(async () => {});
    pathname = `/recordings/${recording.id}`;
    rerender(watcherPage());
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
  });

  it.each([paused, interrupted])("closes on Not now and leaves $status untouched and badged", async (recording) => {
    pathname = `/recordings/${recording.id}`;
    api.getUnfinishedCaptures.mockResolvedValue([recording]);
    render(<CaptureProvider><CaptureShell>
      <WatcherState /><RecordingAttentionBadge recordingId={recording.id} />
    </CaptureShell></CaptureProvider>);
    expect(await screen.findByRole("dialog")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Not now" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(screen.getByText(/needs attention/)).toBeVisible();
    // A later check keeps it closed, and nothing was sent about the recording.
    const checks = api.getUnfinishedCaptures.mock.calls.length;
    fireEvent.click(screen.getByRole("button", { name: "Check unfinished captures" }));
    await waitFor(() => expect(api.getUnfinishedCaptures).toHaveBeenCalledTimes(checks + 1));
    await act(async () => {});
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(api.discardRecordingCapture).not.toHaveBeenCalled();
    expect(api.finalizeRecordingCapture).not.toHaveBeenCalled();
    expect(api.resumeRecordingCapture).not.toHaveBeenCalled();
  });

  it("does not reopen recovery when the holder releases its lock while this page stays open", async () => {
    pathname = `/recordings/${paused.id}`;
    held = [{ name: `nojoin-capture:${paused.id}`, mode: "shared" }];
    const { rerender } = render(watcherPage());
    await act(async () => {});
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    held = [];
    fireEvent.click(screen.getByRole("button", { name: "Check unfinished captures" }));
    await waitFor(() => expect(screen.getByTestId("attention-state")).toHaveTextContent(paused.id));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    pathname = "/recordings";
    rerender(watcherPage());
    await act(async () => {});
    pathname = `/recordings/${paused.id}`;
    rerender(watcherPage());
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
  });

  it("does not open on the destination when the previous page's recovery check answers late", async () => {
    pathname = `/recordings/${paused.id}`;
    let answer!: (recordings: Recording[]) => void;
    const check = new Promise<Recording[]>((resolve) => { answer = resolve; });
    api.getUnfinishedCaptures.mockReturnValue(check);
    const { rerender } = render(watcherPage());
    pathname = "/recordings/another-meeting";
    rerender(watcherPage());
    await act(async () => { answer([paused]); await check; });

    expect(screen.getByTestId("attention-state")).toHaveTextContent(paused.id);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});

describe("Start reservation across entry surfaces", () => {
  it("reserves before init so two Start buttons initialise only one recording", async () => {
    api.getUnfinishedCaptures.mockResolvedValue([]);
    api.discardRecordingCapture.mockResolvedValue(undefined);
    pickCaptureSource.mockRejectedValue(new PickSourceError("display_cancelled", "Picker cancelled"));
    render(<CaptureProvider><CaptureShell>
      <MeetingControls variant="dashboard" /><MeetingControls variant="sidebar" />
    </CaptureShell></CaptureProvider>);
    await act(async () => {});
    let answer!: (response: { id: string; name: string }) => void;
    const init = new Promise<{ id: string; name: string }>((resolve) => { answer = resolve; });
    api.initRecording.mockReturnValueOnce(init);
    const [dashboard, sidebar] = screen.getAllByRole("button", { name: "Start Meeting" });

    fireEvent.click(dashboard);
    fireEvent.click(sidebar);
    expect(api.initRecording).toHaveBeenCalledOnce();
    await act(async () => { answer({ id: "rec-new", name: "New meeting" }); await init; });

    await waitFor(() => expect(pickCaptureSource).toHaveBeenCalledOnce());
    expect(api.initRecording).toHaveBeenCalledOnce();
    expect(routerPush).not.toHaveBeenCalled();
    expect(screen.getAllByRole("button", { name: "Start Meeting" })).toHaveLength(2);
  });
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

    await expect(capture.refreshPausedRecording({ onDemand: true })).resolves.toEqual(paused);
    expect(capture.getState().pausedRecording).toEqual(paused);

    held.push({ name: "nojoin-capture:rec-paused", mode: "shared" });
    await expect(capture.refreshPausedRecording()).resolves.toBeNull();
    expect(capture.getState().pausedRecording).toBeNull();
  });

  it("marks a locally remembered capture after its lock is released without offering a dialog", async () => {
    retainPausedContext();
    vi.useFakeTimers();
    const capture = controller();
    await capture.refreshPausedRecording();
    expect(capture.getState().pausedRecording).toBeNull();

    held = [];
    await vi.advanceTimersByTimeAsync(30_000);

    expect(api.getUnfinishedCaptures).toHaveBeenCalledTimes(2);
    expect(capture.getState().attentionRecordings).toEqual([paused]);
    expect(capture.getState().pausedRecording).toBeNull();
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

  it.each(["held", "pending"])("never explicitly offers a same-browser %s capture", async (kind) => {
    if (kind === "pending") { pending = held; held = []; }
    const capture = controller();
    await expect(capture.refreshPausedRecording({ onDemand: true })).resolves.toBeNull();
  });
});

describe("CaptureShell watching a paused capture", () => {
  it("keeps the dialog shut after lock release while the same list remains open", async () => {
    // Retained context now identifies attention; only page entry or an action opens recovery.
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
    await waitFor(() => expect(screen.getByTestId("attention-state")).toHaveTextContent(paused.id));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("keeps a list reload quiet even with this tab's retained context", async () => {
    retainPausedContext();
    held = [];
    const oldPage = controller();
    await oldPage.destroy();
    render(<CaptureProvider><CaptureShell><WatcherState /></CaptureShell></CaptureProvider>);

    await waitFor(() => expect(screen.getByTestId("attention-state")).toHaveTextContent(paused.id));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByTestId("watcher-state")).toHaveTextContent("watching:none");
    expect(api.discardRecordingCapture).not.toHaveBeenCalled();
  });

  it("marks stalled UPLOADING without interrupting a list with its dialog", async () => {
    held = [];
    api.getUnfinishedCaptures.mockResolvedValue([interrupted]);
    render(<CaptureProvider><CaptureShell><WatcherState /></CaptureShell></CaptureProvider>);

    await waitFor(() => expect(screen.getByTestId("attention-state")).toHaveTextContent(paused.id));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(api.discardRecordingCapture).not.toHaveBeenCalled();
  });

  it.each(["held", "pending"])("never flags or offers a same-browser %s capture on its page", async (kind) => {
    pathname = `/recordings/${paused.id}`;
    if (kind === "pending") { pending = held; held = []; }
    render(<CaptureProvider><CaptureShell><WatcherState /></CaptureShell></CaptureProvider>);
    await act(async () => {});

    expect(api.getUnfinishedCaptures).toHaveBeenCalled();
    expect(screen.getByTestId("attention-state")).toBeEmptyDOMElement();
    expect(screen.getByTestId("watcher-state")).toHaveTextContent("watching:none");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("leaves recording-page Stop and Discard disabled after a held capture's 409", async () => {
    pathname = `/recordings/${paused.id}`;
    api.initRecording.mockRejectedValue(activeRecordingConflict(paused));
    vi.spyOn(console, "error").mockImplementation(() => {});
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<CaptureProvider><CaptureShell>
      <MeetingControls variant="dashboard" /><LiveMeetingControls size="bar" /><WatcherState />
    </CaptureShell></CaptureProvider>);
    await act(async () => {});
    fireEvent.click(screen.getByRole("button", { name: "Start Meeting" }));
    await waitFor(() => expect(useNotificationStore.getState().activeNotifications).toEqual([
      expect.objectContaining({ type: "error", message: inAnotherTabMessage }),
    ]));

    expect(screen.getByTitle("Stop recording")).toBeDisabled();
    expect(screen.getByTitle("Discard recording")).toBeDisabled();
    fireEvent.click(screen.getByTitle("Stop recording"));
    fireEvent.click(screen.getByTitle("Discard recording"));
    expect(api.finalizeRecordingCapture).not.toHaveBeenCalled();
    expect(api.discardRecordingCapture).not.toHaveBeenCalled();
    expect(confirm).not.toHaveBeenCalled();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(api.initRecording).toHaveBeenCalledOnce();
    expect(pickCaptureSource).not.toHaveBeenCalled();
    expect(routerPush).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Start Meeting" })).toBeEnabled();
  });
});

describe("Start while another tab or device records", () => {
  const inProgressMessage = "Another recording is still in progress. Stop it before starting a new one. "
    + "If the tab recording it was closed, Nojoin offers to resume or discard it within two minutes.";
  const live = { ...paused, status: RecordingStatus.UPLOADING, client_status: ClientStatus.RECORDING } as Recording;

  it.each([
    { where: "another tab of this browser", locked: true, toast: inAnotherTabMessage },
    { where: "another device", locked: false, toast: inProgressMessage },
  ])("names $where in its refusal", async ({ locked, toast }) => {
    pathname = "/";
    held = locked ? [{ name: `nojoin-capture:${live.id}`, mode: "shared" }] : [];
    api.getUnfinishedCaptures.mockResolvedValue([live]);
    api.initRecording.mockRejectedValue(activeRecordingConflict(live, inProgressMessage));
    vi.spyOn(console, "error").mockImplementation(() => {});
    render(<CaptureProvider><CaptureShell><MeetingControls variant="dashboard" /></CaptureShell></CaptureProvider>);
    await act(async () => {});

    fireEvent.click(screen.getByRole("button", { name: "Start Meeting" }));

    await waitFor(() => expect(useNotificationStore.getState().activeNotifications).toEqual([
      expect.objectContaining({ type: "error", message: toast }),
    ]));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(pickCaptureSource).not.toHaveBeenCalled();
  });
});

describe("the recording page in a tab that is not capturing", () => {
  const live = {
    ...paused, id: "rec-live", status: RecordingStatus.UPLOADING, client_status: ClientStatus.RECORDING,
  } as Recording;

  it.each([
    { recording: live, serverPaused: false, label: "Live", minuteLater: "13:34", resume: false },
    { recording: paused, serverPaused: true, label: "Paused", minuteLater: "12:34", resume: true },
  ])("shows $label and the server's audio clock", async ({
    recording, serverPaused, label, minuteLater, resume,
  }) => {
    vi.useFakeTimers();
    pathname = `/recordings/${recording.id}`;
    // Another tab of this browser is capturing it.
    held = [{ name: `nojoin-capture:${recording.id}`, mode: "exclusive" }];
    api.getUnfinishedCaptures.mockResolvedValue([recording]);
    // The page's audio clock, fed the detail read as the page feeds it.
    let now = 0;
    const audio = new AudioClock(() => now);
    audio.observe({ ...recording, live_audio_ms: 754_000 });
    render(<CaptureProvider>
      <LiveMeetingControls
        size="bar"
        serverPaused={serverPaused}
        audioNow={() => audio.audioNow(recording.id)}
      />
      <WatcherState />
    </CaptureProvider>);
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });

    expect(api.getUnfinishedCaptures).toHaveBeenCalled();
    expect(screen.getByTestId("watcher-state")).toHaveTextContent("watching:none");
    expect(screen.getByText(label)).toBeInTheDocument();
    expect(screen.queryByText("Recording")).not.toBeInTheDocument();
    expect(screen.getByText("12:34")).toBeInTheDocument();
    expect(screen.getByTitle("Stop recording")).toBeDisabled();
    // No Resume beside "Live": there is nothing paused to resume.
    expect(Boolean(screen.queryByTitle("Resume recording"))).toBe(resume);

    now += 60_000;
    await act(async () => { await vi.advanceTimersByTimeAsync(1_000); });

    expect(screen.getByText(minuteLater)).toBeInTheDocument();
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

      const starts = entry === "dashboard" || entry === "sidebar";
      // Start asks the server, which refuses while the paused recording is
      // unfinished; the dialog then speaks for the refusal, with no toast.
      api.initRecording.mockRejectedValue(activeRecordingConflict(paused));
      const opener = screen.getByRole("button", { name: starts ? "Start Meeting" : "Import Audio" });
      expect(opener).toBeEnabled();
      fireEvent.click(opener);

      expect(await screen.findByRole("dialog")).toBeInTheDocument();
      expect(screen.getByText("Recording paused")).toBeInTheDocument();
      expect(screen.getByText(/still.*open on another device.*finish it there/)).toBeInTheDocument();
      expect(screen.getByText(paused.name)).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Stop and process" })).toBeEnabled();
      expect(useImportAudioStore.getState().isImportAudioOpen).toBe(false);
      expect(api.initRecording).toHaveBeenCalledTimes(starts ? 1 : 0);
      expect(api.discardRecordingCapture).not.toHaveBeenCalled();
      expect(api.resumeRecordingCapture).not.toHaveBeenCalled();
      expect(api.finalizeRecordingCapture).not.toHaveBeenCalled();
      expect(pickCaptureSource).not.toHaveBeenCalled();
      expect(routerPush).not.toHaveBeenCalled();
      await act(async () => {});
      expect(useNotificationStore.getState().activeNotifications).toEqual([]);
    },
  );
});
