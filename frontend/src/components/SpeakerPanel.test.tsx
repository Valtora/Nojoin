import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import SpeakerPanel from "./SpeakerPanel";
import type {
  GlobalSpeaker,
  RecordingSpeaker,
  TranscriptSegment,
} from "@/types";

const addNotification = vi.fn();
const updateSpeaker = vi.fn();
const deleteRecordingSpeaker = vi.fn();

vi.mock("@/lib/notificationStore", () => ({
  useNotificationStore: () => ({ addNotification }),
}));

vi.mock("@/lib/api", () => ({
  updateSpeaker: (...a: unknown[]) => updateSpeaker(...a),
  mergeRecordingSpeakers: vi.fn(),
  deleteRecordingSpeaker: (...a: unknown[]) => deleteRecordingSpeaker(...a),
  extractVoiceprint: vi.fn(),
  promoteToGlobalSpeaker: vi.fn(),
  acceptSpeakerNameSuggestion: vi.fn(),
  rejectSpeakerNameSuggestion: vi.fn(),
}));

// Capture the items handed to the context menu so the action set is testable
// without depending on the menu's DOM rendering.
let lastMenuItems: { label: string; onClick: () => void }[] = [];
vi.mock("./ContextMenu", () => ({
  default: ({ items }: { items: { label: string; onClick: () => void }[] }) => {
    lastMenuItems = items;
    return <div data-testid="context-menu" />;
  },
}));

vi.mock("./ConfirmationModal", () => ({
  default: ({ isOpen, onConfirm }: { isOpen: boolean; onConfirm: () => void }) =>
    isOpen ? (
      <button data-testid="confirm-delete" onClick={onConfirm}>
        Confirm
      </button>
    ) : null,
}));

vi.mock("./VoiceprintModal", () => ({ default: () => null }));
vi.mock("./people/SplitPersonModal", () => ({ default: () => null }));
vi.mock("./ColorPicker", () => ({
  InlineColorPicker: () => <div data-testid="color-picker" />,
}));

function buildSpeaker(
  overrides: Partial<RecordingSpeaker> &
    Pick<RecordingSpeaker, "diarization_label">,
): RecordingSpeaker {
  return {
    id: overrides.id ?? 1,
    created_at: "2026-05-20T00:00:00Z",
    updated_at: "2026-05-20T00:00:00Z",
    recording_id: "rec-1",
    diarization_label: overrides.diarization_label,
    local_name: overrides.local_name,
    name: overrides.name,
    global_speaker_id: overrides.global_speaker_id,
    global_speaker: overrides.global_speaker,
    has_voiceprint: overrides.has_voiceprint,
    merged_into_id: overrides.merged_into_id,
  } as RecordingSpeaker;
}

function buildSegment(
  overrides: Partial<TranscriptSegment> &
    Pick<TranscriptSegment, "speaker" | "start" | "end">,
): TranscriptSegment {
  return {
    id: overrides.id,
    text: overrides.text ?? "hello",
    speaker: overrides.speaker,
    start: overrides.start,
    end: overrides.end,
  } as TranscriptSegment;
}

function renderPanel(
  overrides: Partial<React.ComponentProps<typeof SpeakerPanel>> = {},
) {
  const onPlaySegment = vi.fn();
  const onRefresh = vi.fn();
  const speakers: RecordingSpeaker[] = overrides.speakers ?? [
    buildSpeaker({ diarization_label: "SPEAKER_00", local_name: "Alice" }),
    buildSpeaker({ diarization_label: "SPEAKER_01", local_name: "Bob", id: 2 }),
  ];
  const segments: TranscriptSegment[] = overrides.segments ?? [
    buildSegment({ speaker: "SPEAKER_00", start: 0, end: 2 }),
    buildSegment({ speaker: "SPEAKER_01", start: 2, end: 4 }),
  ];
  const globalSpeakers: GlobalSpeaker[] = overrides.globalSpeakers ?? [];

  render(
    <SpeakerPanel
      speakers={speakers}
      segments={segments}
      onPlaySegment={onPlaySegment}
      recordingId="rec-1"
      speakerColors={{}}
      onColorChange={vi.fn()}
      currentTime={999}
      isPlaying={false}
      onPause={vi.fn()}
      onResume={vi.fn()}
      onRefresh={onRefresh}
      globalSpeakers={globalSpeakers}
      {...overrides}
    />,
  );

  return { onPlaySegment, onRefresh, speakers, segments };
}

describe("SpeakerPanel", () => {
  beforeEach(() => {
    addNotification.mockReset();
    updateSpeaker.mockReset().mockResolvedValue(undefined);
    deleteRecordingSpeaker.mockReset().mockResolvedValue(undefined);
    lastMenuItems = [];
  });

  it("groups speakers into rows by display name", () => {
    renderPanel();
    expect(screen.getByText("Alice")).toBeInTheDocument();
    expect(screen.getByText("Bob")).toBeInTheDocument();
  });

  it("plays a snippet for the speaker when the preview button is pressed", () => {
    const { onPlaySegment } = renderPanel();

    const previewButtons = screen.getAllByTitle("Preview Voice");
    fireEvent.click(previewButtons[0]);

    expect(onPlaySegment).toHaveBeenCalledTimes(1);
    // Alice's only segment spans 0..2
    expect(onPlaySegment).toHaveBeenCalledWith(0, 2);
  });

  it("exposes the recording-speaker context menu action set", () => {
    renderPanel();

    fireEvent.contextMenu(screen.getByText("Alice"));

    const labels = lastMenuItems.map((item) => item.label);
    expect(labels).toEqual([
      "Rename / Assign",
      "Merge into...",
      "Split / Unmerge Speaker",
      "Create Voiceprint",
      "Add to People",
      "Delete",
    ]);
  });

  it("renames every member label and refreshes on submit", async () => {
    const { onRefresh } = renderPanel({
      speakers: [
        buildSpeaker({ diarization_label: "SPEAKER_00", local_name: "Alice" }),
      ],
      segments: [buildSegment({ speaker: "SPEAKER_00", start: 0, end: 2 })],
    });

    fireEvent.doubleClick(screen.getByText("Alice"));
    const input = screen.getByDisplayValue("Alice");
    fireEvent.change(input, { target: { value: "Alicia" } });
    fireEvent.keyDown(input, { key: "Enter" });

    await waitFor(() => {
      expect(updateSpeaker).toHaveBeenCalledWith("rec-1", "SPEAKER_00", "Alicia");
    });
    await waitFor(() => expect(onRefresh).toHaveBeenCalled());
  });

  it("deletes a speaker after confirmation", async () => {
    const { onRefresh } = renderPanel({
      speakers: [
        buildSpeaker({ diarization_label: "SPEAKER_00", local_name: "Alice" }),
      ],
      segments: [buildSegment({ speaker: "SPEAKER_00", start: 0, end: 2 })],
    });

    fireEvent.contextMenu(screen.getByText("Alice"));
    const deleteItem = lastMenuItems.find((item) => item.label === "Delete");
    expect(deleteItem).toBeDefined();
    deleteItem?.onClick();

    fireEvent.click(await screen.findByTestId("confirm-delete"));

    await waitFor(() => {
      expect(deleteRecordingSpeaker).toHaveBeenCalledWith("rec-1", "SPEAKER_00");
    });
    await waitFor(() => expect(onRefresh).toHaveBeenCalled());
  });
});
