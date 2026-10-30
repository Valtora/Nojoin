import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import RecordingCaptureSlot from "./RecordingCaptureSlot";
import { ClientStatus, RecordingStatus, type Recording } from "@/types";

const discard = vi.fn();
const cancel = vi.fn();
const pause = vi.fn();
const resume = vi.fn();
const stop = vi.fn();
const capture = {
  controller: { getState: () => ({ error: null }) },
  recordingId: null as string | null,
  ownedRecordingId: null as string | null,
  pausedRecording: null as { id: string } | null,
  status: "idle",
  runtimeActive: false,
  elapsedSeconds: 12,
  cancel, pause, resume, stop,
  coverageWarning: null as {
    capturedSeconds: number; elapsedSeconds: number; missingSeconds: number;
    queuedSeconds: number; cause: string;
  } | null,
  dismissCoverageWarning: vi.fn(),
  stopStage: null as string | null,
  uploadQueue: { segments: 0, seconds: 0 },
};

vi.mock("@/lib/capture/CaptureProvider", () => ({ useCapture: () => capture }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("./recordings/_hooks/useRecordingActions", () => ({
  useRecordingActions: () => ({ discard }),
}));
vi.mock("@/lib/notificationStore", () => ({
  useNotificationStore: () => ({ addNotification: vi.fn() }),
}));
vi.mock("@/lib/api", () => ({ updateRecordingMaxSpeakers: vi.fn() }));
vi.mock("./LiveAudioWaveform", () => ({
  default: ({ recordingId, enabled }: { recordingId: string; enabled: boolean }) => (
    <div data-testid="waveform" data-recording={recordingId} data-enabled={String(enabled)} />
  ),
}));
vi.mock("./DocumentUploadModal", () => ({
  default: ({ isOpen, onSuccess, recordingId }: {
    isOpen: boolean; onSuccess: () => void; recordingId: string;
  }) => isOpen ? <button onClick={onSuccess}>Upload for {recordingId}</button> : null,
}));

const recording = (overrides: Partial<Recording> = {}): Recording => ({
  id: "rec-1", created_at: "2026-10-27T10:00:00Z", updated_at: "2026-10-27T10:00:00Z",
  name: "Weekly sync", meeting_uid: "meeting-1", audio_path: "/tmp/audio.wav",
  status: RecordingStatus.PROCESSING, duration_seconds: 600, processing_progress: 40,
  discardable: true, is_archived: false, is_deleted: false, ...overrides,
});

const ownCapture = (status = "recording") => Object.assign(capture, {
  recordingId: "rec-1", ownedRecordingId: "rec-1", runtimeActive: true, status,
});

describe("RecordingCaptureSlot", () => {
  beforeEach(() => {
    Object.assign(capture, { recordingId: null, ownedRecordingId: null, pausedRecording: null, status: "idle",
      runtimeActive: false, coverageWarning: null, stopStage: null, uploadQueue: { segments: 0, seconds: 0 } });
    discard.mockReset().mockResolvedValue(undefined);
    cancel.mockReset();
    pause.mockReset();
    resume.mockReset();
    stop.mockReset().mockResolvedValue({ ok: true });
  });
  afterEach(() => vi.restoreAllMocks());

  it.each([
    ["Live", RecordingStatus.UPLOADING, ClientStatus.RECORDING],
    ["Paused", RecordingStatus.PAUSED, ClientStatus.PAUSED],
    ["Uploading", RecordingStatus.UPLOADING, ClientStatus.UPLOADING],
    ["Queued", RecordingStatus.QUEUED, ClientStatus.IDLE],
    ["Processing", RecordingStatus.PROCESSING, ClientStatus.IDLE],
  ] as const)("shows server %s without local transport for an observer", (label, status, client_status) => {
    render(<RecordingCaptureSlot recording={recording({ status, client_status })} />);
    expect(screen.getByText(label)).toBeInTheDocument();
    expect(screen.queryByTestId("waveform")).not.toBeInTheDocument();
    expect(screen.queryByTitle("Stop recording")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Attach Docs" })).toBeInTheDocument();
  });

  it.each([RecordingStatus.PROCESSED, RecordingStatus.ERROR])("leaves the player slot to settled audio in %s", (status) => {
    const { container } = render(<RecordingCaptureSlot recording={recording({ status })} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("mounts matching waveform, elapsed time, speaker cap and working local transport", async () => {
    ownCapture();
    render(<RecordingCaptureSlot recording={recording({ status: RecordingStatus.UPLOADING, client_status: ClientStatus.RECORDING })} />);
    expect(screen.getByTestId("waveform")).toHaveAttribute("data-recording", "rec-1");
    expect(screen.getByTestId("waveform")).toHaveAttribute("data-enabled", "true");
    expect(screen.getByText("00:12")).toBeInTheDocument();
    expect(screen.getByLabelText(/max speakers/i)).toBeEnabled();
    fireEvent.click(screen.getByTitle("Pause recording"));
    await waitFor(() => expect(pause).toHaveBeenCalledTimes(1));
  });

  it("never exposes another recording's waveform or transport", () => {
    ownCapture();
    render(<RecordingCaptureSlot recording={recording({ id: "rec-2", status: RecordingStatus.UPLOADING, client_status: ClientStatus.RECORDING })} />);
    expect(screen.getByText("Live")).toBeInTheDocument();
    expect(screen.queryByTestId("waveform")).not.toBeInTheDocument();
    expect(screen.queryByTitle("Pause recording")).not.toBeInTheDocument();
    expect(screen.queryByTitle("Stop recording")).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/max speakers/i)).not.toBeInTheDocument();
  });

  it("keeps file imports free of browser capture controls while another meeting records", () => {
    ownCapture();
    render(<RecordingCaptureSlot recording={recording({ id: "import-1", status: RecordingStatus.UPLOADING, client_status: undefined, upload_progress: 37 })} />);
    expect(screen.getByText("Uploading")).toBeInTheDocument();
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "37");
    expect(screen.queryByTestId("waveform")).not.toBeInTheDocument();
    expect(screen.queryByTitle("Stop recording")).not.toBeInTheDocument();
  });

  it.each(["recording", "paused"])("uses local %s while metadata is delayed", (status) => {
    ownCapture(status);
    render(<RecordingCaptureSlot recording={recording({ status: RecordingStatus.PROCESSED })} />);
    expect(screen.getByText(status === "recording" ? "Recording" : "Paused")).toBeInTheDocument();
    expect(screen.getByTitle("Stop recording")).toBeEnabled();
    expect(screen.getByTestId("waveform")).toBeInTheDocument();
  });

  it("keeps pause/resume disabled without runtime tracks but still permits Stop", async () => {
    ownCapture("paused");
    capture.runtimeActive = false;
    render(<RecordingCaptureSlot recording={recording({ status: RecordingStatus.PAUSED })} />);
    expect(screen.getByTitle("Resume recording")).toBeDisabled();
    expect(screen.getByLabelText(/max speakers/i)).toBeDisabled();
    expect(screen.getByTestId("waveform")).toHaveAttribute("data-enabled", "false");
    fireEvent.click(screen.getByTitle("Stop recording"));
    await waitFor(() => expect(stop).toHaveBeenCalledTimes(1));
  });

  it("keeps Stop upload stages and backlog guarded even before metadata catches up", () => {
    ownCapture("finalizing");
    capture.stopStage = "flushing-uploads";
    capture.uploadQueue = { segments: 20, seconds: 40 };
    render(<RecordingCaptureSlot recording={recording({ status: RecordingStatus.UPLOADING, client_status: ClientStatus.RECORDING })} />);
    expect(screen.getByText("Uploading")).toBeInTheDocument();
    expect(screen.getByText(/Uploading the last 00:40 of audio/)).toBeInTheDocument();
    expect(screen.getByTitle("Resume recording")).toBeDisabled();
    expect(screen.getByTitle("Stop recording")).toBeDisabled();
    expect(screen.getByTitle("Discard recording")).toBeDisabled();
    expect(screen.queryByTestId("waveform")).not.toBeInTheDocument();
  });

  it("keeps the finishing stage when metadata has already settled", () => {
    ownCapture("finalizing");
    capture.stopStage = "finalizing";
    render(<RecordingCaptureSlot recording={recording({ status: RecordingStatus.PROCESSED })} />);
    expect(screen.getByText("Finishing")).toBeInTheDocument();
    expect(screen.getByTitle("Stop recording")).toBeDisabled();
  });

  it("retains local coverage warnings", () => {
    ownCapture();
    capture.coverageWarning = { capturedSeconds: 12, elapsedSeconds: 80, missingSeconds: 68, queuedSeconds: 0, cause: "unknown" };
    render(<RecordingCaptureSlot recording={recording({ status: RecordingStatus.UPLOADING, client_status: ClientStatus.RECORDING })} />);
    expect(screen.getByText("00:12 captured")).toBeInTheDocument();
  });

  it("shows processing step, progress, ETA and confirmed shared discard", async () => {
    const confirmed = vi.spyOn(window, "confirm").mockReturnValue(false);
    const onDiscarded = vi.fn();
    render(<RecordingCaptureSlot recording={recording({ processing_step: "Identifying speakers", processing_eta_seconds: 63 })} onDiscarded={onDiscarded} />);
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "40");
    expect(screen.getByText("Identifying speakers")).toBeInTheDocument();
    expect(screen.getByText("1m 3s remaining")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Discard Recording" }));
    expect(discard).not.toHaveBeenCalled();
    confirmed.mockReturnValue(true);
    fireEvent.click(screen.getByRole("button", { name: "Discard Recording" }));
    await waitFor(() => expect(discard).toHaveBeenCalledWith("rec-1", {
      onSuccess: onDiscarded, onError: expect.any(Function),
    }));
    expect(screen.getByRole("button", { name: "Discard Recording" })).toBeDisabled();
    expect(cancel).not.toHaveBeenCalled();
  });

  it.each([RecordingStatus.QUEUED, RecordingStatus.PROCESSING])(
    "offers no Discard on a finished meeting back in %s, as notes regenerate or it is reprocessed (#833)",
    (status) => {
      render(<RecordingCaptureSlot recording={recording({ status, discardable: false, processing_step: "Generating notes" })} />);
      expect(screen.getByText("Generating notes")).toBeInTheDocument();
      expect(screen.getByRole("progressbar")).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Discard Recording" })).not.toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Attach Docs" })).toBeInTheDocument();
    },
  );

  it("offers no Discard when the server has not said the recording can be discarded", () => {
    render(<RecordingCaptureSlot recording={recording({ discardable: undefined })} />);
    expect(screen.queryByRole("button", { name: "Discard Recording" })).not.toBeInTheDocument();
  });

  it("allows another discard attempt after the shared action reports failure", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    discard.mockImplementation(async (_id: string, options: { onError: () => void }) => options.onError());
    render(<RecordingCaptureSlot recording={recording()} />);
    fireEvent.click(screen.getByRole("button", { name: "Discard Recording" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Discard Recording" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "Discard Recording" }));
    await waitFor(() => expect(discard).toHaveBeenCalledTimes(2));
  });

  it("opens Attach Docs for the displayed recording and signals its lists after upload", () => {
    const updated = vi.fn();
    window.addEventListener("recording-documents-updated", updated);
    try {
      render(<RecordingCaptureSlot recording={recording()} />);
      fireEvent.click(screen.getByRole("button", { name: "Attach Docs" }));
      fireEvent.click(screen.getByRole("button", { name: "Upload for rec-1" }));
      expect(updated).toHaveBeenCalledTimes(1);
      expect(updated.mock.calls[0][0].detail).toEqual({ recordingId: "rec-1" });
    } finally {
      window.removeEventListener("recording-documents-updated", updated);
    }
  });
});
