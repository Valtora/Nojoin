import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { RecordingStatus, type Recording, type TranscriptSegment } from "@/types";

import TranscriptSection from "./TranscriptSection";

const confirmSpeakerName = vi.fn();
const dismissSpeakerName = vi.fn();
const addNotification = vi.fn();

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  confirmSpeakerName: (...args: unknown[]) => confirmSpeakerName(...args),
  dismissSpeakerName: (...args: unknown[]) => dismissSpeakerName(...args),
}));
vi.mock("@/lib/notificationStore", () => ({
  useNotificationStore: () => ({ addNotification }),
}));

const originalScrollIntoView = Element.prototype.scrollIntoView;
beforeAll(() => {
  Element.prototype.scrollIntoView = vi.fn();
});
afterAll(() => {
  Element.prototype.scrollIntoView = originalScrollIntoView;
});

const SEGMENTS: TranscriptSegment[] = [
  { id: "u0", speaker: "SPEAKER_0", start: 0, end: 2, text: "Hi, I'm Alex." },
  { id: "u1", speaker: "SPEAKER_1", start: 2, end: 4, text: "Thanks, Alex. Alexander sends his best." },
];

const RECORDING = {
  id: "rec-1",
  name: "Weekly sync",
  status: RecordingStatus.PROCESSED,
  duration_seconds: 60,
  speakers: [
    {
      id: 1,
      recording_id: "rec-1",
      diarization_label: "SPEAKER_0",
      local_name: "Alex",
      name_source: "heuristic",
      name_pending: true,
    },
  ],
} as unknown as Recording;

const renderSection = () => {
  const onRefresh = vi.fn();
  const onFindAndReplace = vi.fn();
  render(
    <TranscriptSection
      active
      recording={RECORDING}
      timelineNotes={[]}
      transcriptSegments={SEGMENTS}
      currentTime={-1}
      isPlaying={false}
      speakerMap={{ SPEAKER_0: "Alex", SPEAKER_1: "Speaker 2" }}
      speakerColors={{}}
      globalSpeakers={[]}
      canUndo={false}
      canRedo={false}
      deferredTranscriptUtteranceIds={[]}
      onPlaySegment={vi.fn()}
      onPause={vi.fn()}
      onResume={vi.fn()}
      onRenameSpeaker={vi.fn()}
      onRefresh={onRefresh}
      onUpdateSegmentSpeaker={vi.fn()}
      onUpdateSegmentText={vi.fn()}
      onFindAndReplace={onFindAndReplace}
      onUndo={vi.fn()}
      onRedo={vi.fn()}
      onExport={vi.fn()}
      onActiveEditUtteranceChange={vi.fn()}
    />,
  );
  return { onRefresh, onFindAndReplace };
};

const openReview = () =>
  fireEvent.click(screen.getByRole("button", { name: "Confirm or dismiss the name Alex" }));

describe("TranscriptSection suggested names", () => {
  beforeEach(() => {
    confirmSpeakerName.mockReset().mockResolvedValue([]);
    dismissSpeakerName.mockReset().mockResolvedValue([]);
    addNotification.mockReset();
  });

  it("confirms a line's suggested name through the API and reloads the recording", async () => {
    const { onRefresh } = renderSection();

    openReview();
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));

    await waitFor(() => expect(onRefresh).toHaveBeenCalledTimes(1));
    expect(confirmSpeakerName).toHaveBeenCalledWith("rec-1", "SPEAKER_0");
    expect(addNotification).toHaveBeenCalledWith({ type: "success", message: "Confirmed Alex." });
  });

  it("dismisses it and leaves the words of the transcript alone", async () => {
    // The server puts back "Speaker 1" and carries it into the notes itself.
    dismissSpeakerName.mockResolvedValue([
      {
        id: 1,
        recording_id: "rec-1",
        diarization_label: "SPEAKER_0",
        local_name: null,
        name: "Speaker 1",
        name_source: null,
        name_pending: false,
      },
    ]);
    const { onRefresh, onFindAndReplace } = renderSection();

    openReview();
    fireEvent.click(screen.getByRole("button", { name: "Not Alex" }));

    await waitFor(() => expect(onRefresh).toHaveBeenCalledTimes(1));
    expect(dismissSpeakerName).toHaveBeenCalledWith("rec-1", "SPEAKER_0");
    expect(onFindAndReplace).not.toHaveBeenCalled();
    expect(addNotification).toHaveBeenCalledWith({
      type: "success",
      message: "Removed the name Alex.",
    });
  });

  it("reports a refused decision and still reloads, so the view catches up", async () => {
    confirmSpeakerName.mockRejectedValue({
      isAxiosError: true,
      response: { status: 409, data: { detail: "This speaker has no name awaiting confirmation" } },
    });
    const { onRefresh } = renderSection();

    openReview();
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));

    await waitFor(() => expect(onRefresh).toHaveBeenCalledTimes(1));
    expect(addNotification).toHaveBeenCalledWith({
      type: "error",
      message: "This speaker has no name awaiting confirmation",
    });
  });
});
