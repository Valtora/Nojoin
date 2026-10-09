import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  act,
  fireEvent,
  renderWithProviders,
  screen,
  waitFor,
} from "@/test/renderWithProviders";
import {
  RecordingStatus,
  type Recording,
  type TranscriptUtteranceList,
} from "@/types";

const routerPush = vi.fn();
const routerRefresh = vi.fn();
const addNotification = vi.fn();
const setActivePanel = vi.fn();
const exportAudio = vi.fn();

const getRecording = vi.fn();
const getSettings = vi.fn();
const getGlobalSpeakers = vi.fn();
const getTranscriptUtterances = vi.fn();
const renameRecording = vi.fn();
const generateNotes = vi.fn();

let activePanel = "transcript";

// Stable across renders, like Next's own router: the page's load callback
// depends on it, and a fresh object per render would reload in a loop.
const router = { push: routerPush, refresh: routerRefresh };

vi.mock("next/navigation", () => ({
  useRouter: () => router,
}));

vi.mock("@/lib/notificationStore", () => ({
  useNotificationStore: () => ({ addNotification }),
}));

// The dashboard layout wraps every recording page in the capture provider; the
// shared recording actions read it.
vi.mock("@/lib/capture/CaptureProvider", () => ({
  useCapture: () => ({
    cancel: vi.fn(),
    recordingId: null,
    pausedRecording: null,
    runtimeActive: false,
  }),
}));

vi.mock("@/lib/store", () => ({
  useNavigationStore: () => ({
    chatPanelHeight: 30,
    setChatPanelHeight: vi.fn(),
    activePanel,
    setActivePanel,
  }),
}));

vi.mock("@/lib/api", () => ({
  getRecording: (...args: unknown[]) => getRecording(...args),
  getSettings: (...args: unknown[]) => getSettings(...args),
  getGlobalSpeakers: (...args: unknown[]) => getGlobalSpeakers(...args),
  getTranscriptUtterances: (...args: unknown[]) => getTranscriptUtterances(...args),
  renameRecording: (...args: unknown[]) => renameRecording(...args),
  updateSettings: vi.fn(),
  updateSpeaker: vi.fn(),
  updateTranscriptSegmentSpeaker: vi.fn(),
  updateTranscriptUtteranceSpeaker: vi.fn(),
  updateTranscriptSegmentText: vi.fn(),
  updateTranscriptUtteranceText: vi.fn(),
  findAndReplace: vi.fn(),
  updateSpeakerColor: vi.fn(),
  generateNotes: (...args: unknown[]) => generateNotes(...args),
  updateNotes: vi.fn(),
  updateUserNotes: vi.fn(),
  updateMeetingEdgeFocus: vi.fn(),
  exportContent: vi.fn(),
  exportAudio: (...args: unknown[]) => exportAudio(...args),
  ExportContentType: {},
  ExportFormat: {},
}));

// Heavy child components are stubbed so the tests pin the page's own
// orchestration (which panel/section renders, what data it receives) rather
// than the children's internals.
vi.mock("@/components/ChatPanel", () => ({
  default: () => <div data-testid="chat-panel" />,
}));
vi.mock("@/components/AudioPlayer", () => ({
  default: ({ recording }: { recording: Recording }) => (
    <div data-testid="audio-player" data-has-audio={String(recording.has_audio)} />
  ),
}));
vi.mock("@/components/SpeakerPanel", () => ({
  default: () => <div data-testid="speaker-panel" />,
}));
vi.mock("@/components/TranscriptView", () => ({
  default: ({ segments }: { segments: unknown[] }) => (
    <div data-testid="transcript-view">segments:{segments.length}</div>
  ),
}));
vi.mock("@/components/NotesView", () => ({
  default: ({
    notes,
    onGenerateNotes,
  }: {
    notes: string | null;
    onGenerateNotes: () => void;
  }) => (
    <div data-testid="notes-view">
      {notes ?? "no-notes"}
      <button type="button" onClick={() => onGenerateNotes()}>
        Generate notes
      </button>
    </div>
  ),
}));
vi.mock("@/components/DocumentsView", () => ({
  default: () => <div data-testid="documents-view" />,
}));
vi.mock("@/components/RecordingStatusDisplay", () => ({
  default: () => <div data-testid="recording-status-display" />,
}));
vi.mock("@/components/ExportModal", () => ({
  default: ({
    hasAudio,
    onExport,
  }: {
    hasAudio: boolean;
    onExport: (contentType: string, format: string) => void;
  }) => (
    <button
      data-testid="export-modal"
      data-has-audio={String(hasAudio)}
      onClick={() => onExport("audio", "txt")}
    />
  ),
}));
vi.mock("@/components/RecordingTagEditor", () => ({
  default: () => <div data-testid="recording-tag-editor" />,
}));
vi.mock("@/components/LinkedEventPanel", () => ({
  default: () => <div data-testid="linked-event-panel" />,
}));

import RecordingPage from "./page";
import {
  AUDIO_RECHECK_INTERVAL_MS,
  AUDIO_RECHECK_WINDOW_MS,
} from "./_hooks/recordingDetailUtils";

const buildRecording = (overrides: Partial<Recording> = {}): Recording => ({
  id: "rec-1",
  created_at: "2026-06-01T10:00:00Z",
  updated_at: "2026-06-01T10:00:00Z",
  name: "Quarterly sync",
  meeting_uid: "meeting-1",
  audio_path: "/tmp/audio.wav",
  duration_seconds: 600,
  status: RecordingStatus.PROCESSED,
  has_proxy: true,
  is_archived: false,
  is_deleted: false,
  tags: [],
  speakers: [],
  transcript: {
    id: 1,
    created_at: "2026-06-01T10:00:00Z",
    updated_at: "2026-06-01T10:00:00Z",
    recording_id: "rec-1",
    segments: [
      { start: 0, end: 2, text: "Hello there", speaker: "SPEAKER_00" },
      { start: 2, end: 4, text: "Hi back", speaker: "SPEAKER_01" },
    ],
    notes: "Generated notes body",
    notes_status: "completed",
  },
  ...overrides,
});

const buildUtteranceList = (
  overrides: Partial<TranscriptUtteranceList> = {},
): TranscriptUtteranceList => ({
  recording_id: "rec-1",
  revision: 1,
  utterances: [],
  tombstones: [],
  speakers: [],
  ...overrides,
});

const renderPage = () =>
  renderWithProviders(<RecordingPage params={Promise.resolve({ id: "rec-1" })} />);

describe("RecordingPage (detail)", () => {
  beforeEach(() => {
    activePanel = "transcript";
    routerPush.mockReset();
    routerRefresh.mockReset();
    addNotification.mockReset();
    setActivePanel.mockReset();
    exportAudio.mockReset();
    getRecording.mockReset();
    getSettings.mockReset();
    getGlobalSpeakers.mockReset();
    getTranscriptUtterances.mockReset();
    renameRecording.mockReset();
    generateNotes.mockReset();

    getRecording.mockResolvedValue(buildRecording());
    getSettings.mockResolvedValue({
      enable_meeting_edge: true,
      meeting_edge_context_level: 2,
    });
    getGlobalSpeakers.mockResolvedValue([]);
    getTranscriptUtterances.mockResolvedValue(buildUtteranceList());
    renameRecording.mockResolvedValue(undefined);
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("shows a loading state before the recording resolves", () => {
    getRecording.mockReturnValue(new Promise(() => {}));
    renderPage();
    expect(screen.getByText("Loading...")).toBeInTheDocument();
  });

  it("loads the recording and renders the transcript section by default", async () => {
    renderPage();

    expect(
      await screen.findByRole("heading", { name: /Quarterly sync/ }),
    ).toBeInTheDocument();

    await waitFor(() => {
      expect(getRecording).toHaveBeenCalledWith("rec-1");
    });
    expect(getGlobalSpeakers).toHaveBeenCalled();

    const transcript = await screen.findByTestId("transcript-view");
    expect(transcript).toHaveTextContent("segments:2");
    expect(screen.getByTestId("notes-view")).toHaveTextContent(
      "Generated notes body",
    );
    expect(screen.getByTestId("documents-view")).toBeInTheDocument();
  });

  it("renders the live status display while the recording is in flight", async () => {
    getRecording.mockResolvedValue(
      buildRecording({ status: RecordingStatus.PROCESSING }),
    );

    renderPage();

    expect(
      await screen.findByTestId("recording-status-display"),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("transcript-view")).not.toBeInTheDocument();
    // In-flight recordings must not request transcript utterances.
    expect(getTranscriptUtterances).not.toHaveBeenCalled();
  });

  it("loads transcript utterances once the recording is settled", async () => {
    renderPage();

    await waitFor(() => {
      expect(getTranscriptUtterances).toHaveBeenCalledWith("rec-1", undefined);
    });
  });

  it("renames the recording through the title editor and refreshes the route", async () => {
    renderPage();

    const heading = await screen.findByRole("heading", { name: /Quarterly sync/ });
    fireEvent.click(heading);

    const input = screen.getByDisplayValue("Quarterly sync");
    fireEvent.change(input, { target: { value: "Renamed sync" } });
    fireEvent.keyDown(input, { key: "Enter" });

    await waitFor(() => {
      expect(renameRecording).toHaveBeenCalledWith("rec-1", "Renamed sync");
    });
    expect(routerRefresh).toHaveBeenCalled();
  });

  it("redirects to the recordings list when loading the recording fails", async () => {
    getRecording.mockRejectedValue(new Error("boom"));
    vi.spyOn(console, "error").mockImplementation(() => {});

    renderPage();

    await waitFor(() => {
      expect(routerPush).toHaveBeenCalledWith("/recordings");
    });
    expect(addNotification).toHaveBeenCalledWith({
      type: "error",
      message: "Failed to load recording.",
    });
  });

  it("picks up audio that arrives after the page reported it unavailable", async () => {
    // A restore commits the recording rows before it moves their audio into
    // place, so a page opened in between first sees no audio at all.
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      let audioOnDisk = false;
      getRecording.mockImplementation(async () =>
        buildRecording({ has_proxy: false, has_audio: audioOnDisk }),
      );

      renderPage();

      expect(await screen.findByTestId("audio-player")).toHaveAttribute(
        "data-has-audio",
        "false",
      );

      audioOnDisk = true;
      await act(async () => {
        await vi.advanceTimersByTimeAsync(AUDIO_RECHECK_INTERVAL_MS);
      });

      expect(screen.getByTestId("audio-player")).toHaveAttribute(
        "data-has-audio",
        "true",
      );
    } finally {
      vi.useRealTimers();
    }
  });

  it("stops re-checking for audio that has not arrived within the window", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      getRecording.mockImplementation(async () =>
        buildRecording({ has_proxy: false, has_audio: false }),
      );

      renderPage();
      await screen.findByTestId("audio-player");
      const loads = getRecording.mock.calls.length;

      await act(async () => {
        await vi.advanceTimersByTimeAsync(AUDIO_RECHECK_WINDOW_MS);
      });
      const checksInWindow = getRecording.mock.calls.length - loads;
      expect(checksInWindow).toBeGreaterThan(0);

      await act(async () => {
        await vi.advanceTimersByTimeAsync(AUDIO_RECHECK_WINDOW_MS);
      });
      expect(getRecording.mock.calls.length - loads).toBe(checksInWindow);
    } finally {
      vi.useRealTimers();
    }
  });

  it("offers no audio export, and explains a failed one, when the audio is gone", async () => {
    getRecording.mockResolvedValue(
      buildRecording({ has_proxy: false, has_audio: false }),
    );
    exportAudio.mockRejectedValue({ response: { status: 404 } });
    vi.spyOn(console, "error").mockImplementation(() => {});

    renderPage();

    const exportModal = await screen.findByTestId("export-modal");
    expect(exportModal).toHaveAttribute("data-has-audio", "false");

    fireEvent.click(exportModal);

    await waitFor(() => {
      expect(addNotification).toHaveBeenCalledWith({
        type: "error",
        message: expect.stringMatching(/audio is not available/),
      });
    });
    expect(exportAudio).toHaveBeenCalledWith("rec-1", "Quarterly sync");
  });

  it("renders the notes panel when notes is the active tab", async () => {
    activePanel = "notes";
    renderPage();

    expect(await screen.findByTestId("notes-view")).toHaveTextContent(
      "Generated notes body",
    );
  });
  // On a phone the page floats the chat button over whichever tab is open.
  // The tabs' scroll regions only leave room for it because the page sets
  // --floating-action-clearance on the mobile container; if that goes, the
  // last transcript line is back under the button.
  it("reserves room for the phone chat button around the tab content", async () => {
    vi.stubGlobal("innerWidth", 390);
    vi.stubGlobal("innerHeight", 844);
    renderPage();

    expect(
      await screen.findByRole("button", { name: "Open Meeting Chat" }),
    ).toBeInTheDocument();
    expect(clearanceAncestors(await screen.findByTestId("transcript-view"))).toHaveLength(1);
  });

  it("reserves nothing on desktop, where there is no floating button", async () => {
    vi.stubGlobal("innerWidth", 1440);
    vi.stubGlobal("innerHeight", 900);
    renderPage();

    const transcript = await screen.findByTestId("transcript-view");
    expect(screen.queryByRole("button", { name: "Open Meeting Chat" })).toBeNull();
    expect(clearanceAncestors(transcript)).toHaveLength(0);
  });

  it("does not toast a notes error that was already there when the page opened", async () => {
    getRecording.mockResolvedValue(
      buildRecording({
        transcript: {
          ...buildRecording().transcript!,
          notes_status: "error",
          error_message: "No model selected for anthropic",
        },
      }),
    );

    renderPage();
    await screen.findByTestId("transcript-view");

    expect(addNotification).not.toHaveBeenCalled();
  });

  it("shows why notes cannot be generated for a failed transcription", async () => {
    const detail =
      "Transcription failed; reprocess the recording before generating notes.";
    generateNotes.mockRejectedValue({ response: { status: 409, data: { detail } } });
    vi.spyOn(console, "error").mockImplementation(() => {});
    activePanel = "notes";
    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "Generate notes" }));

    await waitFor(() => {
      expect(addNotification).toHaveBeenCalledWith({ type: "error", message: detail });
    });
  });
});

function clearanceAncestors(element: HTMLElement): HTMLElement[] {
  const found: HTMLElement[] = [];
  for (let node: HTMLElement | null = element; node; node = node.parentElement) {
    if (/\[--floating-action-clearance:/.test(node.className)) found.push(node);
  }
  return found;
}
