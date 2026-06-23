import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
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

const getRecording = vi.fn();
const getSettings = vi.fn();
const getGlobalSpeakers = vi.fn();
const getTranscriptUtterances = vi.fn();
const renameRecording = vi.fn();

let activePanel = "transcript";

vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push: routerPush,
    refresh: routerRefresh,
  }),
}));

vi.mock("@/lib/notificationStore", () => ({
  useNotificationStore: () => ({ addNotification }),
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
  generateNotes: vi.fn(),
  updateNotes: vi.fn(),
  updateUserNotes: vi.fn(),
  updateMeetingEdgeFocus: vi.fn(),
  exportContent: vi.fn(),
  exportAudio: vi.fn(),
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
  default: () => <div data-testid="audio-player" />,
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
  default: ({ notes }: { notes: string | null }) => (
    <div data-testid="notes-view">{notes ?? "no-notes"}</div>
  ),
}));
vi.mock("@/components/DocumentsView", () => ({
  default: () => <div data-testid="documents-view" />,
}));
vi.mock("@/components/RecordingStatusDisplay", () => ({
  default: () => <div data-testid="recording-status-display" />,
}));
vi.mock("@/components/ExportModal", () => ({
  default: () => <div data-testid="export-modal" />,
}));
vi.mock("@/components/RecordingTagEditor", () => ({
  default: () => <div data-testid="recording-tag-editor" />,
}));
vi.mock("@/components/LinkedEventPanel", () => ({
  default: () => <div data-testid="linked-event-panel" />,
}));

import RecordingPage from "./page";

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
    getRecording.mockReset();
    getSettings.mockReset();
    getGlobalSpeakers.mockReset();
    getTranscriptUtterances.mockReset();
    renameRecording.mockReset();

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

  it("renders the notes panel when notes is the active tab", async () => {
    activePanel = "notes";
    renderPage();

    expect(await screen.findByTestId("notes-view")).toHaveTextContent(
      "Generated notes body",
    );
  });
});
