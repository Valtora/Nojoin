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
const promoteToGlobalSpeaker = vi.fn();
const deleteRecordingSpeaker = vi.fn();
const getMergedRecordingSpeakers = vi.fn();
const unmergeRecordingSpeaker = vi.fn();
const confirmSpeakerName = vi.fn();
const dismissSpeakerName = vi.fn();

vi.mock("@/lib/notificationStore", () => ({
  useNotificationStore: () => ({ addNotification }),
}));

vi.mock("@/lib/api", () => ({
  updateSpeaker: (...a: unknown[]) => updateSpeaker(...a),
  mergeRecordingSpeakers: vi.fn(),
  deleteRecordingSpeaker: (...a: unknown[]) => deleteRecordingSpeaker(...a),
  extractVoiceprint: vi.fn(),
  promoteToGlobalSpeaker: (...a: unknown[]) => promoteToGlobalSpeaker(...a),
  getMergedRecordingSpeakers: (...a: unknown[]) =>
    getMergedRecordingSpeakers(...a),
  unmergeRecordingSpeaker: (...a: unknown[]) => unmergeRecordingSpeaker(...a),
  confirmSpeakerName: (...a: unknown[]) => confirmSpeakerName(...a),
  dismissSpeakerName: (...a: unknown[]) => dismissSpeakerName(...a),
}));

beforeEach(() => {
  getMergedRecordingSpeakers.mockReset().mockResolvedValue([]);
  unmergeRecordingSpeaker.mockReset().mockResolvedValue({});
});

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
    name_last_edit_source: overrides.name_last_edit_source,
    library_voiceprint: overrides.library_voiceprint,
    name_source: overrides.name_source,
    name_pending: overrides.name_pending,
    identity_confidence: overrides.identity_confidence,
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
    updateSpeaker.mockReset().mockResolvedValue([]);
    promoteToGlobalSpeaker.mockReset();
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

  it("says when a rename left the person without a voiceprint, and why", async () => {
    updateSpeaker.mockResolvedValue([
      buildSpeaker({
        diarization_label: "SPEAKER_00",
        global_speaker_id: 7,
        library_voiceprint: {
          status: "skipped_insufficient_speech",
          speech_seconds: 12.4,
          required_speech_seconds: 20,
        },
      }),
    ]);
    renderPanel({
      speakers: [
        buildSpeaker({ diarization_label: "SPEAKER_00", local_name: "Alice" }),
      ],
      segments: [buildSegment({ speaker: "SPEAKER_00", start: 0, end: 2 })],
    });

    fireEvent.doubleClick(screen.getByText("Alice"));
    const input = screen.getByDisplayValue("Alice");
    fireEvent.change(input, { target: { value: "Dana" } });
    fireEvent.keyDown(input, { key: "Enter" });

    await waitFor(() =>
      expect(addNotification).toHaveBeenCalledWith({
        type: "info",
        message:
          "Voiceprint not saved: 12.4 s of clear speech, 20 s needed. A later recording linked to this person with at least 20 s of clear speech will save it.",
      }),
    );
  });

  it("says when a promote kept the person's voiceprint from drifting", async () => {
    promoteToGlobalSpeaker.mockResolvedValue(
      buildSpeaker({
        diarization_label: "SPEAKER_00",
        global_speaker: { id: 7, name: "Alice" } as GlobalSpeaker,
        library_voiceprint: {
          status: "skipped_drift",
          speech_seconds: 40,
          required_speech_seconds: 20,
        },
      }),
    );
    renderPanel({
      speakers: [
        buildSpeaker({ diarization_label: "SPEAKER_00", local_name: "Alice" }),
      ],
      segments: [buildSegment({ speaker: "SPEAKER_00", start: 0, end: 2 })],
    });

    fireEvent.contextMenu(screen.getByText("Alice"));
    lastMenuItems.find((item) => item.label === "Add to People")?.onClick();

    await waitFor(() =>
      expect(addNotification).toHaveBeenCalledWith({
        type: "info",
        message:
          "Voiceprint not updated: this speaker sounds too unlike Alice's saved voiceprint. Check that the name is right.",
      }),
    );
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

describe("speaker naming provenance", () => {
  it("discloses a speaker an assistant named", () => {
    renderPanel({
      speakers: [
        buildSpeaker({
          diarization_label: "SPEAKER_00",
          local_name: "Mila Faramji",
          name_last_edit_source: "mcp",
        }),
      ],
      segments: [buildSegment({ speaker: "SPEAKER_00", start: 0, end: 2 })],
    });

    expect(screen.getByText("AI named")).toBeInTheDocument();
  });

  it("stays silent for a name set in the web app", () => {
    renderPanel({
      speakers: [
        buildSpeaker({
          diarization_label: "SPEAKER_00",
          local_name: "Mila Faramji",
          name_last_edit_source: "api",
        }),
      ],
      segments: [buildSegment({ speaker: "SPEAKER_00", start: 0, end: 2 })],
    });

    expect(screen.queryByText("AI named")).not.toBeInTheDocument();
  });

  it("stays silent when no source was recorded", () => {
    renderPanel({
      speakers: [
        buildSpeaker({
          diarization_label: "SPEAKER_00",
          local_name: "Mila Faramji",
        }),
      ],
      segments: [buildSegment({ speaker: "SPEAKER_00", start: 0, end: 2 })],
    });

    expect(screen.queryByText("AI named")).not.toBeInTheDocument();
  });
});

describe("automatic merges", () => {
  it("lists a cluster processing merged in and undoes it", async () => {
    getMergedRecordingSpeakers.mockResolvedValue([
      {
        id: 7,
        public_id: "rs-7",
        diarization_label: "SPEAKER_07",
        name: "Speaker 7",
        merged_into_id: 1,
        merged_into_label: "SPEAKER_00",
        restorable_utterance_count: 3,
        has_voiceprint: true,
      },
    ]);
    const { onRefresh } = renderPanel();

    expect(
      await screen.findByText("Merged in: SPEAKER_07, 3 lines"),
    ).toBeInTheDocument();
    expect(getMergedRecordingSpeakers).toHaveBeenCalledWith("rec-1");

    fireEvent.click(screen.getByText("Undo merge"));

    await waitFor(() => {
      expect(unmergeRecordingSpeaker).toHaveBeenCalledWith(
        "rec-1",
        "SPEAKER_07",
      );
    });
    await waitFor(() => expect(onRefresh).toHaveBeenCalled());
  });

  it("offers nothing when processing merged no cluster", async () => {
    renderPanel();

    await waitFor(() => expect(getMergedRecordingSpeakers).toHaveBeenCalled());
    expect(screen.queryByText("Undo merge")).not.toBeInTheDocument();
  });
});

describe("suggested names", () => {
  beforeEach(() => {
    addNotification.mockReset();
    confirmSpeakerName.mockReset().mockResolvedValue([]);
    dismissSpeakerName.mockReset().mockResolvedValue([]);
  });

  const pendingAlice = buildSpeaker({
    diarization_label: "SPEAKER_00",
    local_name: "Alice",
    name_source: "voiceprint",
    identity_confidence: 0.823,
    name_pending: true,
  });
  const bob = buildSpeaker({
    diarization_label: "SPEAKER_01",
    local_name: "Bob",
    id: 2,
    name_source: "llm",
  });

  it("marks only a pending name with a question mark and says why it was suggested", () => {
    renderPanel({ speakers: [pendingAlice, bob] });

    expect(screen.getByText("Alice?")).toBeInTheDocument();
    expect(screen.getByText("Recognised by voice (82%)")).toBeInTheDocument();
    expect(screen.getByText("Bob")).toBeInTheDocument();
    expect(screen.queryByText("Bob?")).not.toBeInTheDocument();
    expect(screen.queryByText("Suggested by AI")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Confirm Alice" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Confirm Bob" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Not Bob" })).not.toBeInTheDocument();
  });

  it("confirms the pending name by its label and refreshes", async () => {
    const { onRefresh } = renderPanel({ speakers: [pendingAlice, bob] });

    fireEvent.click(screen.getByRole("button", { name: "Confirm Alice" }));

    await waitFor(() => expect(onRefresh).toHaveBeenCalledTimes(1));
    expect(confirmSpeakerName).toHaveBeenCalledWith("rec-1", "SPEAKER_00");
    expect(dismissSpeakerName).not.toHaveBeenCalled();
    expect(addNotification).toHaveBeenCalledWith({ type: "success", message: "Confirmed Alice." });
  });

  it("dismisses the name, carries the new one into the notes, and refreshes", async () => {
    dismissSpeakerName.mockResolvedValue([
      buildSpeaker({ diarization_label: "SPEAKER_00", local_name: "Speaker 1" }),
    ]);
    const onSpeakerRenamed = vi.fn();
    const { onRefresh } = renderPanel({ speakers: [pendingAlice, bob], onSpeakerRenamed });

    fireEvent.click(screen.getByRole("button", { name: "Not Alice" }));

    await waitFor(() => expect(onRefresh).toHaveBeenCalledTimes(1));
    expect(dismissSpeakerName).toHaveBeenCalledWith("rec-1", "SPEAKER_00");
    expect(onSpeakerRenamed).toHaveBeenCalledWith("Alice", "Speaker 1");
    expect(confirmSpeakerName).not.toHaveBeenCalled();
  });

  it("ignores a second click while a decision is in flight", async () => {
    let finish = (value: unknown) => value;
    confirmSpeakerName.mockImplementation(() => new Promise((resolve) => (finish = resolve)));
    const { onRefresh } = renderPanel({ speakers: [pendingAlice, bob] });

    fireEvent.click(screen.getByRole("button", { name: "Confirm Alice" }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Not Alice" })).toHaveAttribute("aria-disabled", "true"),
    );
    fireEvent.click(screen.getByRole("button", { name: "Confirm Alice" }));
    fireEvent.click(screen.getByRole("button", { name: "Not Alice" }));
    finish([]);

    await waitFor(() => expect(onRefresh).toHaveBeenCalledTimes(1));
    expect(confirmSpeakerName).toHaveBeenCalledTimes(1);
    expect(dismissSpeakerName).not.toHaveBeenCalled();
  });

  it("sends the pending member's label for a grouped row whose representative is settled", async () => {
    const alex = { id: 7, name: "Alex" } as GlobalSpeaker;
    renderPanel({
      speakers: [
        buildSpeaker({ diarization_label: "SPEAKER_00", global_speaker_id: 7, global_speaker: alex }),
        buildSpeaker({
          diarization_label: "SPEAKER_03",
          id: 4,
          global_speaker_id: 7,
          global_speaker: alex,
          name_source: "heuristic",
          name_pending: true,
        }),
      ],
      segments: [
        buildSegment({ speaker: "SPEAKER_00", start: 0, end: 2 }),
        buildSegment({ speaker: "SPEAKER_00", start: 2, end: 4 }),
        buildSegment({ speaker: "SPEAKER_03", start: 4, end: 6 }),
      ],
      globalSpeakers: [alex],
    });

    expect(screen.getByText("Introduced themselves")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Confirm Alex" }));

    await waitFor(() => expect(confirmSpeakerName).toHaveBeenCalledWith("rec-1", "SPEAKER_03"));
  });

  it("offers Confirm all only when two or more names are pending", () => {
    renderPanel({ speakers: [pendingAlice, bob] });

    expect(screen.queryByRole("button", { name: /Confirm all/ })).not.toBeInTheDocument();
  });

  it("confirms every pending name once and notifies once", async () => {
    const pendingBob = buildSpeaker({
      diarization_label: "SPEAKER_01",
      local_name: "Bob",
      id: 2,
      name_source: "llm",
      name_pending: true,
    });
    const carol = buildSpeaker({ diarization_label: "SPEAKER_02", local_name: "Carol", id: 3 });
    const { onRefresh } = renderPanel({ speakers: [pendingAlice, pendingBob, carol] });

    fireEvent.click(screen.getByRole("button", { name: "Confirm all (2)" }));

    await waitFor(() => expect(onRefresh).toHaveBeenCalledTimes(1));
    expect(confirmSpeakerName.mock.calls).toEqual([
      ["rec-1", "SPEAKER_00"],
      ["rec-1", "SPEAKER_01"],
    ]);
    expect(addNotification.mock.calls).toEqual([
      [{ type: "success", message: "Confirmed 2 names." }],
    ]);
  });

  it("says how many of them were confirmed when one fails", async () => {
    confirmSpeakerName.mockResolvedValueOnce([]).mockRejectedValueOnce(new Error("offline"));
    const pendingBob = buildSpeaker({
      diarization_label: "SPEAKER_01",
      local_name: "Bob",
      id: 2,
      name_source: "user",
      name_pending: true,
    });
    const { onRefresh } = renderPanel({ speakers: [pendingAlice, pendingBob] });

    fireEvent.click(screen.getByRole("button", { name: "Confirm all (2)" }));

    await waitFor(() => expect(onRefresh).toHaveBeenCalledTimes(1));
    expect(addNotification.mock.calls).toEqual([
      [{ type: "error", message: "Confirmed 1 of 2 names. Try the others again." }],
    ]);
  });
});
