import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterAll, afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { HeadNoteOwnerProvider } from "@/lib/notes/HeadNoteOwner";
import { AudioClock } from "@/lib/notes/audioClock";
import { forgetNoteOutboxes, noteOutboxFor } from "@/lib/notes/noteOutboxes";
import { useNoteEdits } from "@/lib/notes/useNoteEdits";
import { ClientStatus, RecordingStatus, type Recording } from "@/types";

import RecordingMyNotes from "./RecordingMyNotes";
import RecordingNoteComposer from "./RecordingNoteComposer";

const track = { readyState: "live" } as MediaStreamTrack;
const capture = {
  runtimeActive: true,
  displayVideoTrack: vi.fn((): MediaStreamTrack | null => track),
};
const grabDisplayFrame = vi.fn(async () => new Blob(["frame"], { type: "image/png" }));
const createTimelineNote = vi.fn(() => new Promise<never>(() => {}));

vi.mock("@/lib/capture/CaptureProvider", () => ({ useCapture: () => capture }));
vi.mock("@/lib/captures/frameImage", async (original) => ({
  ...(await original<typeof import("@/lib/captures/frameImage")>()),
  grabDisplayFrame: (...args: unknown[]) => grabDisplayFrame(...args as []),
}));
vi.mock("@/lib/api", async (original) => ({
  ...(await original<typeof import("@/lib/api")>()),
  createTimelineNote: (...args: unknown[]) => createTimelineNote(...args as []),
}));

const LIVE = {
  id: "capture-integration",
  status: RecordingStatus.UPLOADING,
  client_status: ClientStatus.RECORDING,
  live_audio_ms: 65_000,
  duration_seconds: 600,
  speakers: [],
} as unknown as Recording;
const STOPPED = { ...LIVE, status: RecordingStatus.PROCESSING, client_status: undefined, live_audio_ms: null };
const notes: never[] = [];
const noop = () => {};
const removeWindowListener = window.removeEventListener.bind(window);
const addListener = vi.spyOn(window, "addEventListener");
const removeListener = vi.spyOn(window, "removeEventListener");

const activeKeyListeners = () => {
  const removed = new Set(removeListener.mock.calls.filter(([type]) => type === "keydown")
    .map(([, listener]) => listener));
  return addListener.mock.calls.filter(([type, listener]) => type === "keydown" && !removed.has(listener));
};

/** Real note surfaces share one recording outbox across presentation remounts. */
function Presentation({ recording, mobile, clock }: { recording: Recording; mobile: boolean; clock: AudioClock }) {
  const noteEdits = useNoteEdits(recording.id);
  return <HeadNoteOwnerProvider recordingId={recording.id} timelineNotes={notes}>
    <div key={mobile ? "mobile" : "desktop"} data-testid={mobile ? "mobile-presentation" : "desktop-presentation"}>
      <section aria-label="Transcript">
        <RecordingNoteComposer recording={recording} segments={[]} audioClock={clock} />
      </section>
      <RecordingMyNotes recording={recording} timelineNotes={notes} segments={[]} noteEdits={noteEdits}
        onShowTranscript={noop} onPlay={noop} />
    </div>
  </HeadNoteOwnerProvider>;
}

const shortcut = () => act(async () => {
  fireEvent.keyDown(window, { code: "KeyC", key: "Ç", altKey: true, shiftKey: true });
});

describe("the recording's composed capture ownership", () => {
  beforeEach(() => {
    forgetNoteOutboxes();
    vi.clearAllMocks();
    capture.runtimeActive = true;
    capture.displayVideoTrack.mockReturnValue(track);
  });

  afterEach(() => {
    cleanup();
    // Also cleans deliberately leaked listeners during the negative cleanup probe.
    for (const [type, listener, options] of addListener.mock.calls) {
      removeWindowListener(type, listener, options);
    }
    forgetNoteOutboxes();
  });

  afterAll(() => {
    addListener.mockRestore();
    removeListener.mockRestore();
  });

  it("takes one still per shortcut/camera through remounts and keeps the carried draft/file/clock after Stop", async () => {
    const clock = new AudioClock(() => 0);
    clock.observe(LIVE);
    const outbox = noteOutboxFor(LIVE.id);
    const file = new File(["draft file"], "context.png", { type: "image/png" });
    const view = render(<Presentation recording={LIVE} mobile={false} clock={clock} />);
    expect(screen.getByTestId("desktop-presentation")).toBeInTheDocument();
    expect(activeKeyListeners()).toHaveLength(1);
    act(() => {
      outbox.setDraft({ text: "A carried thought", mode: "send", typingStartMs: 12_000 });
      outbox.setDraftFile(file);
    });
    await shortcut();
    expect(grabDisplayFrame).toHaveBeenCalledExactlyOnceWith(track);
    expect(outbox.getSnapshot().entries).toHaveLength(1);
    expect(outbox.getSnapshot().entries[0]).toMatchObject({ body: "Screen capture", anchorMs: 65_000 });

    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Capture the shared screen" })));
    expect(grabDisplayFrame).toHaveBeenCalledTimes(2);
    expect(outbox.getSnapshot().entries).toHaveLength(2);
    view.rerender(<Presentation recording={LIVE} mobile clock={clock} />);
    expect(screen.getByTestId("mobile-presentation")).toBeInTheDocument();
    expect(activeKeyListeners()).toHaveLength(1);
    await shortcut();
    expect(grabDisplayFrame).toHaveBeenCalledTimes(3);
    expect(outbox.getSnapshot().entries).toHaveLength(3);
    view.rerender(<Presentation recording={LIVE} mobile={false} clock={clock} />);
    expect(screen.getByRole("textbox", { name: "New note" })).toHaveValue("A carried thought");
    expect(outbox.getSnapshot().draftFile).toBe(file);

    capture.runtimeActive = false;
    capture.displayVideoTrack.mockReturnValue(null);
    clock.observe(STOPPED);
    view.rerender(<Presentation recording={STOPPED} mobile={false} clock={clock} />);
    expect(activeKeyListeners()).toHaveLength(0);
    expect(screen.queryByRole("button", { name: "Capture the shared screen" })).toBeNull();
    expect(screen.getByRole("textbox", { name: "New note" })).toHaveValue("A carried thought");
    expect(screen.getByLabelText("Place note")).toHaveValue("send");
    expect(screen.getByTestId("note-draft-file")).toHaveTextContent("context.png");
    expect(screen.getByTestId("live-note-status")).toHaveTextContent("stopped audio clock");
    await shortcut();
    expect(grabDisplayFrame).toHaveBeenCalledTimes(3);
    expect(outbox.getSnapshot().entries).toHaveLength(3);
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Add note" })));
    expect(outbox.getSnapshot().entries).toHaveLength(4);
    expect(outbox.getSnapshot().entries[3]).toMatchObject({ body: "A carried thought", anchorMs: 65_000 });
    expect(outbox.getSnapshot().uploads.get(outbox.getSnapshot().entries[3].id)).toMatchObject({ fileName: "context.png" });
  });

  it.each([false, true])("removes its shortcut on unmount with mobile=%s while the shared track remains available", async (mobile) => {
    const clock = new AudioClock(() => 0);
    clock.observe(LIVE);
    const view = render(<Presentation recording={LIVE} mobile={mobile} clock={clock} />);
    expect(screen.getByTestId(mobile ? "mobile-presentation" : "desktop-presentation")).toBeInTheDocument();
    await shortcut();
    expect(grabDisplayFrame).toHaveBeenCalledOnce();
    const count = noteOutboxFor(LIVE.id).getSnapshot().entries.length;
    view.unmount();
    expect(activeKeyListeners()).toHaveLength(0);
    await shortcut();
    expect(grabDisplayFrame).toHaveBeenCalledOnce();
    expect(noteOutboxFor(LIVE.id).getSnapshot().entries).toHaveLength(count);
  });
});
