import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ClientStatus, RecordingStatus, type Recording } from "@/types";
import RecordingSpeakers from "./RecordingSpeakers";
import { line, panelProps } from "../transcript/transcriptTestFixtures";

const mocks = vi.hoisted(() => ({ name: vi.fn(), notify: vi.fn(), merged: vi.fn(async () => []) }));
vi.mock("@/lib/api/speakers", () => ({ nameLiveSpeaker: mocks.name, getGlobalSpeakers: vi.fn(async () => []) }));
vi.mock("@/lib/api", () => ({ getMergedRecordingSpeakers: mocks.merged }));
vi.mock("@/lib/notificationStore", () => ({ useNotificationStore: () => ({ addNotification: mocks.notify }) }));
vi.mock("../VoiceprintModal", () => ({ default: () => null }));
vi.mock("../people/SplitPersonModal", () => ({ default: () => null }));

const recording: Recording = {
  id: "r1", created_at: "2026-10-27T10:00:00Z", updated_at: "2026-10-27T10:00:00Z",
  name: "Meeting", meeting_uid: "meeting-1", audio_path: "/audio.wav", status: RecordingStatus.UPLOADING,
  client_status: ClientStatus.RECORDING, has_audio: true, has_proxy: false, speakers: [],
  upload_progress: 0, processing_progress: 0, is_archived: false, is_deleted: false,
};
const anchor = line({ speaker: "LIVE_PROVISIONAL_1", settling_required: true, locked: true });
const props = () => ({ ...panelProps(), recording, speakers: [], segments: [anchor], onPeopleChanged: vi.fn(),
  liveTranscript: { speakers: [], segments: [anchor], refresh: vi.fn(), hasLoaded: true,
    liveSpeakerNames: { LIVE_PROVISIONAL_1: "Alex" }, applyLiveSpeakerNames: vi.fn() } });

describe("recording speaker orchestration", () => {
  beforeEach(() => { mocks.name.mockReset().mockResolvedValue({ LIVE_PROVISIONAL_1: "Alex" }); mocks.notify.mockReset(); mocks.merged.mockClear(); });

  it("refreshes People and the feed only after accepting live Add to People", async () => {
    const options = props();
    render(<RecordingSpeakers {...options} />);
    fireEvent.click(screen.getByRole("button", { name: "Add to People" }));
    await waitFor(() => expect(options.onPeopleChanged).toHaveBeenCalledOnce());
    expect(options.liveTranscript.refresh).toHaveBeenCalledOnce();
    expect(options.liveTranscript.applyLiveSpeakerNames).toHaveBeenCalledWith({ LIVE_PROVISIONAL_1: "Alex" });
  });

  it("does not reload merged speakers when only the playback clock changes", async () => {
    const options = props();
    const { rerender } = render(<RecordingSpeakers {...options} />);
    await waitFor(() => expect(mocks.merged).toHaveBeenCalledOnce());
    rerender(<RecordingSpeakers {...options} currentTime={1} />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Alex" })).toBeInTheDocument());
    expect(mocks.merged).toHaveBeenCalledOnce();
  });

  it("retains the identity draft after a stale anchor refusal and refreshes neither People nor feed", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    mocks.name.mockRejectedValue({ response: { data: { detail: "This source changed. Try again." } } });
    const options = props();
    render(<RecordingSpeakers {...options} />);
    fireEvent.click(screen.getByRole("button", { name: "Change identity for Alex" }));
    const input = screen.getByPlaceholderText("Search or add...");
    fireEvent.change(input, { target: { value: "Robin" } });
    fireEvent.keyDown(input, { key: "Enter" });
    await waitFor(() => expect(mocks.notify).toHaveBeenCalledWith({ type: "error", message: "This source changed. Try again." }));
    expect(input).toHaveValue("Robin");
    expect(options.liveTranscript.applyLiveSpeakerNames).not.toHaveBeenCalled();
    expect(options.liveTranscript.refresh).not.toHaveBeenCalled();
    expect(options.onPeopleChanged).not.toHaveBeenCalled();
    vi.restoreAllMocks();
  });
});
