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
const updateSpeaker = vi.fn();
const listTimelineNotes = vi.fn();
const getCurrentUser = vi.fn();

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
    status: "idle",
    stop: vi.fn(),
    uploadQueue: { seconds: 0 },
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
  getCurrentUser: (...args: unknown[]) => getCurrentUser(...args),
  getRecording: (...args: unknown[]) => getRecording(...args),
  getSettings: (...args: unknown[]) => getSettings(...args),
  getGlobalSpeakers: (...args: unknown[]) => getGlobalSpeakers(...args),
  getTranscriptUtterances: (...args: unknown[]) => getTranscriptUtterances(...args),
  renameRecording: (...args: unknown[]) => renameRecording(...args),
  updateSettings: vi.fn(),
  updateSpeaker: (...args: unknown[]) => updateSpeaker(...args),
  updateTranscriptSegmentSpeaker: vi.fn(),
  updateTranscriptUtteranceSpeaker: vi.fn(),
  updateTranscriptSegmentText: vi.fn(),
  updateTranscriptUtteranceText: vi.fn(),
  findAndReplace: vi.fn(),
  updateSpeakerColor: vi.fn(),
  generateNotes: (...args: unknown[]) => generateNotes(...args),
  updateNotes: vi.fn(),
  updateMeetingEdgeFocus: vi.fn(),
  exportContent: vi.fn(),
  exportAudio: (...args: unknown[]) => exportAudio(...args),
  listTimelineNotes: (...args: unknown[]) => listTimelineNotes(...args),
  createTimelineNote: vi.fn(),
  deleteTimelineNote: vi.fn(),
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
  default: ({
    segments,
    onRenameSpeaker,
  }: {
    segments: unknown[];
    onRenameSpeaker: (label: string, newName: string) => void;
  }) => (
    <div data-testid="transcript-view">
      segments:{segments.length}
      <button type="button" onClick={() => onRenameSpeaker("SPEAKER_00", "Dana")}>
        Rename speaker
      </button>
    </div>
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
  default: ({ timelineNotes }: { timelineNotes?: readonly unknown[] | null }) => (
    <div data-testid="recording-status-display">
      notes:{timelineNotes === null ? "loading" : (timelineNotes?.length ?? "none")}
    </div>
  ),
}));
vi.mock("@/components/ExportModal", () => ({
  default: ({
    hasAudio,
    onExport,
  }: {
    hasAudio: boolean;
    onExport: (selection: { kind: "audio" }) => void;
  }) => (
    <button
      data-testid="export-modal"
      data-has-audio={String(hasAudio)}
      onClick={() => onExport({ kind: "audio" })}
    />
  ),
}));
vi.mock("@/components/RecordingTagEditor", () => ({
  default: () => <div data-testid="recording-tag-editor" />,
}));
vi.mock("@/components/LinkedEventPanel", () => ({
  default: () => <div data-testid="linked-event-panel" />,
}));

import SessionSignInDialog from "@/components/SessionSignInDialog";
import { EMPTY_DRAFT } from "@/lib/notes/noteOutbox";
import { forgetNoteOutboxes, noteOutboxFor } from "@/lib/notes/noteOutboxes";
import {
  MemoryNoteStore,
  setNoteStoreForTests,
  storedNotesSignedIn,
} from "@/lib/notes/noteStore";
import {
  holdSignInInPlace,
  noteSignedIn,
  resetSessionForTests,
  useSessionStore,
} from "@/lib/session";

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
    updateSpeaker.mockReset().mockResolvedValue([]);
    listTimelineNotes.mockReset().mockResolvedValue([]);

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

  it("fetches the notes list when the recording opens, and again only when a poll says it changed", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      let signature = "1:aaaa";
      getRecording.mockImplementation(async () =>
        buildRecording({
          status: RecordingStatus.PROCESSING,
          timeline_notes_list_signature: signature,
        }),
      );
      const note = (id: string) => ({
        id,
        recording_id: "rec-1",
        kind: "point",
        body: id,
        anchor_ms: 1_000,
        revision: 1,
        created_at: "2026-10-21T10:00:00",
        updated_at: "2026-10-21T10:00:00",
      });
      listTimelineNotes.mockResolvedValue([note("n1")]);

      renderPage();

      const display = await screen.findByTestId("recording-status-display");
      await waitFor(() => expect(display).toHaveTextContent("notes:1"));
      expect(listTimelineNotes).toHaveBeenCalledTimes(1);

      // A poll with the same signature brings no notes and fetches none.
      await act(async () => {
        await vi.advanceTimersByTimeAsync(3_000);
      });
      expect(getRecording.mock.calls.length).toBeGreaterThan(1);
      expect(listTimelineNotes).toHaveBeenCalledTimes(1);

      // Another device added a note: the next poll's signature differs.
      signature = "2:bbbb";
      listTimelineNotes.mockResolvedValue([note("n1"), note("n2")]);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(3_000);
      });
      await waitFor(() => expect(display).toHaveTextContent("notes:2"));
      expect(listTimelineNotes).toHaveBeenCalledTimes(2);
    } finally {
      vi.useRealTimers();
    }
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

  it("says when renaming a speaker in the transcript did not save a voiceprint", async () => {
    updateSpeaker.mockResolvedValue([
      {
        diarization_label: "SPEAKER_00",
        global_speaker: { id: 7, name: "Dana" },
        library_voiceprint: {
          status: "skipped_insufficient_speech",
          speech_seconds: 12,
          required_speech_seconds: 20,
        },
      },
    ]);
    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "Rename speaker" }));

    await waitFor(() =>
      expect(addNotification).toHaveBeenCalledWith({
        type: "info",
        message:
          "Voiceprint not saved: 12 s of clear speech, 20 s needed. A later recording linked to Dana with at least 20 s of clear speech will save it.",
      }),
    );
    expect(updateSpeaker).toHaveBeenCalledWith("rec-1", "SPEAKER_00", "Dana");
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

describe("RecordingPage (detail): the recording answers 404", () => {
  const ALICE = { id: 1, username: "alice", force_password_change: false };
  const BOB = { id: 2, username: "bob", force_password_change: false };
  const notFound = () => Object.assign(new Error("Not Found"), { response: { status: 404 } });
  const DISCARDED = { type: "info", message: "Recording was discarded or deleted." };
  const ELSEWHERE = /now signed in as (\w+), so this recording can't be updated/;

  // The server: alice's recording, found only under alice's cookie while it
  // exists. Tabs share the cookie, so a sign-in in another tab changes it.
  let cookie: typeof ALICE = ALICE;
  let exists = true;
  // `/users/me` as the real one is: it tells the session and the notes store.
  const whoIsSignedIn = async () => {
    noteSignedIn(cookie);
    void storedNotesSignedIn(cookie.id);
    return cookie;
  };
  let store: MemoryNoteStore;
  let channels: BroadcastChannel[] = [];

  /** Another tab of this browser, on the channel the tabs share. */
  const otherTab = () => {
    const channel = new BroadcastChannel("nojoin-notes");
    channels.push(channel);
    return channel;
  };
  const elsewhereMessages = () =>
    addNotification.mock.calls
      .map(([notification]) => notification.message as string)
      .filter((message) => ELSEWHERE.test(message));

  /** A recording still processing, which the page polls every 3 s. */
  const open = async () => {
    renderPage();
    await screen.findByTestId("recording-status-display");
  };
  /** A processed recording waiting for its proxy: polled every second, with its title on screen. */
  const openWithTitle = async () => {
    getRecording.mockImplementation(async () => {
      if (!exists || cookie.id !== ALICE.id) throw notFound();
      return buildRecording({ has_proxy: false });
    });
    renderPage();
    return screen.findByRole("heading", { name: /Quarterly sync/ });
  };
  const advance = (ms: number) =>
    act(async () => {
      await vi.advanceTimersByTimeAsync(ms);
    });
  const poll = () => advance(3_000);

  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    resetSessionForTests();
    noteSignedIn(ALICE);
    forgetNoteOutboxes();
    store = new MemoryNoteStore();
    setNoteStoreForTests(store);
    cookie = ALICE;
    exists = true;
    routerPush.mockReset();
    addNotification.mockReset();
    getRecording.mockReset().mockImplementation(async () => {
      if (!exists || cookie.id !== ALICE.id) throw notFound();
      return buildRecording({ status: RecordingStatus.PROCESSING });
    });
    getCurrentUser.mockReset().mockImplementation(whoIsSignedIn);
    getSettings.mockResolvedValue({ enable_meeting_edge: true, meeting_edge_context_level: 2 });
    getGlobalSpeakers.mockResolvedValue([]);
    getTranscriptUtterances.mockReset().mockResolvedValue(buildUtteranceList());
    listTimelineNotes.mockReset().mockResolvedValue([]);
  });

  afterEach(() => {
    vi.useRealTimers();
    for (const channel of channels) channel.close();
    channels = [];
    forgetNoteOutboxes();
    setNoteStoreForTests(null);
    holdSignInInPlace(false, "notes");
    resetSessionForTests();
  });

  it("stays on the recording when another account signed in from another tab, and says so once", async () => {
    await open();
    cookie = BOB;

    await poll();
    await poll();

    expect(routerPush).not.toHaveBeenCalled();
    expect(addNotification).not.toHaveBeenCalledWith(DISCARDED);
    expect(addNotification).toHaveBeenCalledTimes(1);
    expect(elsewhereMessages()).toEqual([expect.stringMatching(/now signed in as bob/)]);
    expect(getCurrentUser).toHaveBeenCalledTimes(1);
  });

  it("names the account the server answered with, even one the session doesn't take", async () => {
    await open();
    // bob must change his password first: the session ignores that answer.
    cookie = { ...BOB, force_password_change: true };

    await poll();

    expect(useSessionStore.getState().username).toBe("alice");
    expect(elsewhereMessages()).toEqual([expect.stringMatching(/now signed in as bob/)]);
  });

  it("asks for the owner's sign-in in place when the page holds it, rather than leaving", async () => {
    holdSignInInPlace(true, "notes");
    await open();
    cookie = BOB;

    await poll();

    expect(routerPush).not.toHaveBeenCalled();
    expect(addNotification).not.toHaveBeenCalled();
    expect(useSessionStore.getState()).toMatchObject({ signInRequired: true, otherUsername: "bob" });
  });

  it("asks nothing more while that sign-in dialog is open", async () => {
    holdSignInInPlace(true, "notes");
    await open();
    cookie = BOB;

    await advance(5 * 60_000);
    // A reload of the view asks nothing either, and stays.
    await act(async () => {
      window.dispatchEvent(new CustomEvent("recording-updated", { detail: { id: "rec-1" } }));
      await vi.advanceTimersByTimeAsync(10);
    });

    expect(getCurrentUser).toHaveBeenCalledTimes(1);
    expect(useSessionStore.getState().signInRequired).toBe(true);
    expect(routerPush).not.toHaveBeenCalled();
  });

  it("leaves a recording that is gone for the account that opened it", async () => {
    await open();
    exists = false;

    await poll();

    expect(addNotification).toHaveBeenCalledWith(DISCARDED);
    expect(routerPush).toHaveBeenCalledWith("/recordings");
  });

  it("stays when the recording is found again by the time the account is known", async () => {
    await open();
    exists = false;
    getCurrentUser.mockImplementationOnce(async () => {
      // The owner signs back in from another tab while this is asked.
      exists = true;
      return whoIsSignedIn();
    });

    await poll();

    expect(routerPush).not.toHaveBeenCalled();
    expect(addNotification).not.toHaveBeenCalled();
  });

  it("says so again when another account signs in after the owner was back", async () => {
    await open();
    cookie = BOB;
    await poll();
    cookie = ALICE;
    await poll();

    cookie = BOB;
    await poll();

    expect(elsewhereMessages()).toHaveLength(2);
    expect(routerPush).not.toHaveBeenCalled();
  });

  it("puts the session back once a poll finds the owner back, and stores her notes again", async () => {
    renderWithProviders(
      <>
        <RecordingPage params={Promise.resolve({ id: "rec-1" })} />
        <SessionSignInDialog />
      </>,
    );
    await screen.findByTestId("recording-status-display");
    cookie = BOB;
    await poll();
    expect(useSessionStore.getState()).toMatchObject({ username: "bob", userId: 2 });

    // alice signs back in from a tab whose ping this browser never delivers.
    cookie = ALICE;
    await poll();

    expect(useSessionStore.getState()).toMatchObject({ username: "alice", userId: 1 });
    // Her notes are kept in the browser again.
    act(() => noteOutboxFor("rec-1").setDraft({ ...EMPTY_DRAFT, text: "typed after alice is back" }));
    await waitFor(async () =>
      expect(await store.get(ALICE.id, "rec-1")).toMatchObject({
        draft: { text: "typed after alice is back" },
      }),
    );
    // The text holds sign-in; when her session ends, the dialog asks for hers.
    act(() => useSessionStore.setState({ signInRequired: true }));
    expect(await screen.findByDisplayValue("alice")).toBeInTheDocument();
  });

  it("puts the session back when another tab says the owner signed in", async () => {
    await open();
    cookie = BOB;
    await poll();
    getCurrentUser.mockClear();

    cookie = ALICE;
    otherTab().postMessage({ kind: "kept", accounts: [ALICE.id] });

    await waitFor(() => expect(useSessionStore.getState()).toMatchObject({ userId: 1 }));
    expect(getCurrentUser).toHaveBeenCalledTimes(1);
  });

  it("reads a 404 as a removal again once the owner is back", async () => {
    await open();
    cookie = BOB;
    await poll();
    expect(routerPush).not.toHaveBeenCalled();

    cookie = ALICE;
    await poll();
    exists = false;
    await poll();

    expect(routerPush).toHaveBeenCalledWith("/recordings");
    expect(addNotification).toHaveBeenLastCalledWith(DISCARDED);
  });

  it("still notices a removal made before any poll found the owner back, within a minute", async () => {
    await open();
    cookie = BOB;
    await poll();
    // alice signs back in from another tab and deletes the recording there.
    cookie = ALICE;
    exists = false;

    await advance(30_000);
    expect(routerPush).not.toHaveBeenCalled();
    await advance(33_000);

    expect(routerPush).toHaveBeenCalledWith("/recordings");
    expect(addNotification).toHaveBeenLastCalledWith(DISCARDED);
  });

  it("asks who is signed in at most once a minute while the other account stays", async () => {
    await open();
    cookie = BOB;

    await advance(5 * 60_000);

    // The first 404, then one a minute.
    expect(getCurrentUser.mock.calls.length).toBeLessThanOrEqual(6);
    expect(elsewhereMessages()).toHaveLength(1);
  });

  it("checks one 404 at a time when the server is slow to say who is signed in", async () => {
    getCurrentUser.mockImplementation(
      () => new Promise((resolve) => setTimeout(() => resolve(whoIsSignedIn()), 4_000)),
    );
    await open();
    cookie = BOB;

    for (let i = 0; i < 4; i += 1) await poll();

    expect(getCurrentUser).toHaveBeenCalledTimes(1);
    expect(addNotification).toHaveBeenCalledTimes(1);
  });

  it("leaves once when a removal is checked slowly", async () => {
    getCurrentUser.mockImplementation(
      () => new Promise((resolve) => setTimeout(() => resolve(whoIsSignedIn()), 4_000)),
    );
    await open();
    exists = false;

    for (let i = 0; i < 4; i += 1) await poll();

    expect(routerPush).toHaveBeenCalledTimes(1);
    expect(addNotification).toHaveBeenCalledTimes(1);
  });

  it("does nothing once the page has gone while a check was on its way", async () => {
    getCurrentUser.mockImplementation(
      () => new Promise((resolve) => setTimeout(() => resolve(whoIsSignedIn()), 4_000)),
    );
    const view = renderPage();
    await screen.findByTestId("recording-status-display");
    exists = false;
    await poll();
    expect(getCurrentUser).toHaveBeenCalledTimes(1);

    view.unmount();
    await advance(5_000);

    expect(routerPush).not.toHaveBeenCalled();
    expect(addNotification).not.toHaveBeenCalled();
  });

  it("keeps the account that first loaded the recording through a refresh while the session says bob", async () => {
    await open();
    cookie = BOB;
    await poll();
    // alice is back, and a poll finds the recording, but the session can't
    // be put back yet: it still says bob when the view is refreshed.
    cookie = ALICE;
    getCurrentUser.mockImplementation(async () => Promise.reject(new Error("Network Error")));
    await poll();
    await act(async () => {
      window.dispatchEvent(new CustomEvent("recording-updated", { detail: { id: "rec-1" } }));
      await vi.advanceTimersByTimeAsync(10);
    });
    expect(useSessionStore.getState().username).toBe("bob");
    getCurrentUser.mockImplementation(whoIsSignedIn);

    // bob signs in again from the other tab.
    cookie = BOB;
    await poll();

    expect(routerPush).not.toHaveBeenCalled();
    expect(addNotification).not.toHaveBeenCalledWith(DISCARDED);
  });

  it("keeps it through a title edit too, so alice's own removal is told as one", async () => {
    const heading = await openWithTitle();
    cookie = BOB;
    await advance(1_000);
    cookie = ALICE;
    getCurrentUser.mockImplementation(async () => Promise.reject(new Error("Network Error")));
    // Editing the title loads the recording again.
    fireEvent.click(heading);
    await advance(10);
    expect(useSessionStore.getState().username).toBe("bob");
    getCurrentUser.mockImplementation(whoIsSignedIn);

    exists = false;
    await advance(61_000);

    expect(addNotification).toHaveBeenCalledWith(DISCARDED);
    expect(routerPush).toHaveBeenCalledWith("/recordings");
    expect(elsewhereMessages()).toEqual([expect.stringMatching(/now signed in as bob/)]);
  });

  it("stays when a refresh of the view answers 404 under another account", async () => {
    await open();
    cookie = BOB;

    await act(async () => {
      window.dispatchEvent(new CustomEvent("recording-updated", { detail: { id: "rec-1" } }));
      await vi.advanceTimersByTimeAsync(10);
    });

    expect(routerPush).not.toHaveBeenCalled();
    expect(addNotification).not.toHaveBeenCalledWith({ type: "error", message: "Failed to load recording." });
    expect(elsewhereMessages()).toEqual([expect.stringMatching(/now signed in as bob/)]);
  });

  it("stays on the recording when a poll fails for any other reason", async () => {
    await open();
    getRecording.mockImplementation(async () => {
      throw Object.assign(new Error("Bad Gateway"), { response: { status: 502 } });
    });

    await poll();

    expect(routerPush).not.toHaveBeenCalled();
    expect(getCurrentUser).not.toHaveBeenCalled();
  });

  it("does nothing while it can't tell who is signed in, and asks again a minute later", async () => {
    getCurrentUser.mockRejectedValueOnce(new Error("Network Error"));
    await open();
    exists = false;

    await poll();
    expect(routerPush).not.toHaveBeenCalled();
    expect(addNotification).not.toHaveBeenCalled();

    await advance(30_000);
    expect(getCurrentUser).toHaveBeenCalledTimes(1);
    await advance(30_000);
    expect(getCurrentUser).toHaveBeenCalledTimes(2);
    expect(routerPush).toHaveBeenCalledWith("/recordings");
  });
});
