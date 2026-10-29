import type { ComponentProps } from "react";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ClientStatus, RecordingStatus, type Recording } from "@/types";
import RecordingSpeakers from "./RecordingSpeakers";
import { line, panelProps } from "../transcript/transcriptTestFixtures";
import RecordingMainContent from "@/app/(dashboard)/recordings/[id]/_components/RecordingMainContent";

const mocks = vi.hoisted(() => ({ name: vi.fn(), notify: vi.fn(), merged: vi.fn(async () => []) }));
vi.mock("@/lib/api/speakers", () => ({ nameLiveSpeaker: mocks.name, getGlobalSpeakers: vi.fn(async () => []) }));
vi.mock("@/lib/api", () => ({ getMergedRecordingSpeakers: mocks.merged }));
vi.mock("@/lib/notificationStore", () => ({ useNotificationStore: () => ({ addNotification: mocks.notify }) }));
vi.mock("../VoiceprintModal", () => ({ default: () => null }));
vi.mock("../people/SplitPersonModal", () => ({ default: () => null }));
vi.mock("@/app/(dashboard)/recordings/[id]/_components/RecordingHeader", () => ({ default: () => null }));
vi.mock("@/app/(dashboard)/recordings/[id]/_components/LanguageNoticeBanner", () => ({ default: () => null }));
vi.mock("@/app/(dashboard)/recordings/[id]/_components/TranscriptSection", () => ({ default: () => null }));
vi.mock("@/app/(dashboard)/recordings/[id]/_components/NotesSection", () => ({ default: () => null }));
vi.mock("@/app/(dashboard)/recordings/[id]/_components/DocumentsSection", () => ({ default: () => null }));
vi.mock("@/app/(dashboard)/recordings/[id]/_components/AnalyticsSection", () => ({ default: () => null }));
vi.mock("@/components/notes/CapturesGallery", () => ({ default: () => null }));

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

  it("mounts pending names plainly with Confirm in the actual mobile Speakers tab", async () => {
    const options = props();
    const speakers = [{ id: 1, recording_id: recording.id, diarization_label: "SPEAKER_0", local_name: "Robin",
      name_source: "llm" as const, name_pending: true,
      created_at: recording.created_at, updated_at: recording.updated_at }];
    const content = <RecordingSpeakers {...options} speakers={speakers} />;
    // Other panes/header are mocked; this exercises the mobile content branch
    // and the production recording-speaker/panel chain together.
    const main = { recording, isMobile: true, activePanel: "speakers", setActivePanel: vi.fn(),
      speakerMap: {}, speakersContent: content } as unknown as ComponentProps<typeof RecordingMainContent>;
    const originalScroll = Element.prototype.scrollIntoView;
    Element.prototype.scrollIntoView = vi.fn();
    try {
      render(<RecordingMainContent {...main} />);
      const panel = screen.getByRole("tabpanel", { name: "Speakers" });
      expect(within(panel).getByRole("button", { name: "Robin" })).toBeInTheDocument();
      expect(within(panel).queryByText("?")).not.toBeInTheDocument();
      expect(within(panel).getByRole("button", { name: "Confirm Robin" })).toBeInTheDocument();
      expect(within(panel).getByText("Suggested by AI")).toBeInTheDocument();
      await waitFor(() => expect(mocks.merged).toHaveBeenCalledOnce());
    } finally { Element.prototype.scrollIntoView = originalScroll; }
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
