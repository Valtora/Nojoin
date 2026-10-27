import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import SpeakerPanel from "./SpeakerPanel";
import { matchesStyleDeclaration } from "@/test/styleMarkerContracts";
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
    identity_locked: overrides.identity_locked,
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
  it("blocks voice previews and next snippets while playback is unavailable", () => {
    const onPause = vi.fn();
    const { onPlaySegment } = renderPanel({ disablePlayback: true, currentTime: 1, isPlaying: true, onPause });
    const preview = screen.getByTitle("Pause");
    expect(preview).toBeDisabled();
    fireEvent.click(preview);
    expect(screen.queryByTitle("Next Snippet")).not.toBeInTheDocument();
    expect(onPause).not.toHaveBeenCalled();
    expect(onPlaySegment).not.toHaveBeenCalled();
  });

  beforeEach(() => {
    addNotification.mockReset();
    updateSpeaker.mockReset().mockResolvedValue([]);
    promoteToGlobalSpeaker.mockReset();
    deleteRecordingSpeaker.mockReset().mockResolvedValue(undefined);
    lastMenuItems = [];
  });

  it.each([false, true])("matches its own pane elevation and inset-fill rules (dark=%s)", (dark) => {
    renderPanel();
    const panel = screen.getByRole("complementary");

    for (const style of ["default", "classic", "depth"]) {
      const appearance = { dark, style };
      expect(matchesStyleDeclaration(panel, "styles.css", "--style-chrome-shadow",
        "var(--style-elevation-2)", appearance)).toBe(style === "depth");
      expect(matchesStyleDeclaration(panel, dark ? "depth-dark.css" : "depth-gradient.css",
        dark ? "--depth-fill-base" : "--depth-light-fill", "var(--surface-inset)", appearance))
        .toBe(style === "depth");
    }
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

    // The "?" sits outside the name, which may be truncated.
    expect(screen.getByText("Alice").parentElement).toHaveTextContent(/^Alice\?$/);
    expect(screen.getByText("Alice")).toHaveClass("truncate");
    expect(screen.getByText("?")).not.toHaveClass("truncate");
    expect(screen.getByText("Recognised by voice")).toBeInTheDocument();
    expect(screen.getByText("Bob").parentElement).toHaveTextContent(/^Bob$/);
    expect(screen.getAllByText("?")).toHaveLength(1);
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

  it("shows a strong voice match plainly and dismisses it from the name menu", async () => {
    const { onRefresh } = renderPanel({
      speakers: [buildSpeaker({
        ...pendingAlice,
        name_pending: false,
        identity_confidence: 0.86,
        identity_locked: false,
      }), bob],
    });

    expect(screen.getByText("Alice").parentElement).toHaveTextContent(/^Alice$/);
    expect(screen.queryByText("?")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Confirm Alice" })).not.toBeInTheDocument();
    expect(screen.queryByText("Recognised by voice")).not.toBeInTheDocument();
    fireEvent.contextMenu(screen.getByText("Alice"));
    const dismiss = lastMenuItems.find((item) => item.label === "Not Alice");
    expect(dismiss).toBeDefined();
    await act(async () => { await dismiss?.onClick(); });

    await waitFor(() => expect(onRefresh).toHaveBeenCalledTimes(1));
    expect(dismissSpeakerName.mock.calls).toEqual([["rec-1", "SPEAKER_00"]]);
    expect(confirmSpeakerName).not.toHaveBeenCalled();
  });

  it("dismisses the name and refreshes, leaving the transcript's words alone", async () => {
    // The server puts back "Speaker 1" and carries it into the notes, whole
    // words only. A find-and-replace here would also turn "Thanks, Alice"
    // into "Thanks, Speaker 1" and "Alicester" into "Speaker 1ster".
    // As the API sends it: the cleared local name is null, not absent.
    dismissSpeakerName.mockResolvedValue([
      {
        ...buildSpeaker({ diarization_label: "SPEAKER_00", name: "Speaker 1", name_pending: false }),
        local_name: null,
        name_source: null,
      },
    ]);
    const onSpeakerRenamed = vi.fn();
    const { onRefresh } = renderPanel({ speakers: [pendingAlice, bob], onSpeakerRenamed });

    fireEvent.click(screen.getByRole("button", { name: "Not Alice" }));

    await waitFor(() => expect(onRefresh).toHaveBeenCalledTimes(1));
    expect(dismissSpeakerName).toHaveBeenCalledWith("rec-1", "SPEAKER_00");
    expect(onSpeakerRenamed).not.toHaveBeenCalled();
    expect(confirmSpeakerName).not.toHaveBeenCalled();
  });

  it("shows Confirm and Not with no reason when the source has none to give", () => {
    renderPanel({
      speakers: [buildSpeaker({ ...pendingAlice, name_source: "auto", name_pending: true })],
    });

    expect(screen.getByRole("button", { name: "Confirm Alice" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Not Alice" })).toBeInTheDocument();
    expect(screen.queryByText("Recognised by voice")).not.toBeInTheDocument();
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

  const groupedAlex = () => {
    const alex = { id: 7, name: "Alex" } as GlobalSpeaker;
    const member = (label: string, id: number) =>
      buildSpeaker({
        diarization_label: label,
        id,
        global_speaker_id: 7,
        global_speaker: alex,
        name_source: "voiceprint",
        name_pending: true,
      });
    return {
      speakers: [member("SPEAKER_00", 1), member("SPEAKER_03", 4)],
      segments: [
        buildSegment({ speaker: "SPEAKER_00", start: 0, end: 2 }),
        buildSegment({ speaker: "SPEAKER_03", start: 4, end: 6 }),
      ],
      globalSpeakers: [alex],
    };
  };

  it("confirms every pending member of a grouped row, with one notification", async () => {
    const { onRefresh } = renderPanel(groupedAlex());

    // One row, so no Confirm all; its Confirm settles both members.
    expect(screen.queryByRole("button", { name: /Confirm all/ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Confirm Alex" }));

    await waitFor(() => expect(onRefresh).toHaveBeenCalledTimes(1));
    expect(confirmSpeakerName.mock.calls).toEqual([
      ["rec-1", "SPEAKER_00"],
      ["rec-1", "SPEAKER_03"],
    ]);
    expect(addNotification.mock.calls).toEqual([
      [{ type: "success", message: "Confirmed Alex." }],
    ]);
  });

  it("dismisses every pending member of a grouped row", async () => {
    const { onRefresh } = renderPanel(groupedAlex());

    fireEvent.click(screen.getByRole("button", { name: "Not Alex" }));

    await waitFor(() => expect(onRefresh).toHaveBeenCalledTimes(1));
    expect(dismissSpeakerName.mock.calls).toEqual([
      ["rec-1", "SPEAKER_00"],
      ["rec-1", "SPEAKER_03"],
    ]);
  });

  it("confirms only pending members and dismisses strong matches too in a mixed grouped row", async () => {
    const alex = { id: 7, name: "Alex" } as GlobalSpeaker;
    const member = (label: string, id: number, fields: Partial<RecordingSpeaker>) =>
      buildSpeaker({
        diarization_label: label,
        id,
        global_speaker_id: 7,
        global_speaker: alex,
        ...fields,
      });
    const { onRefresh } = renderPanel({
      speakers: [
        member("SPEAKER_00", 1, { name_source: "voiceprint", name_pending: true }),
        member("SPEAKER_03", 4, {
          name_source: "voiceprint", name_pending: false, identity_locked: false,
          identity_confidence: 0.86,
        }),
        member("SPEAKER_04", 5, {
          name_source: "voiceprint", name_pending: false, identity_locked: true,
        }),
        member("SPEAKER_05", 6, { name_source: "auto", name_pending: false }),
      ],
      segments: [
        buildSegment({ speaker: "SPEAKER_00", start: 0, end: 2 }),
        buildSegment({ speaker: "SPEAKER_03", start: 4, end: 6 }),
        buildSegment({ speaker: "SPEAKER_03", start: 8, end: 10 }),
      ],
      globalSpeakers: [alex],
    });

    fireEvent.click(screen.getByRole("button", { name: "Confirm Alex" }));
    await waitFor(() => expect(onRefresh).toHaveBeenCalledTimes(1));
    expect(confirmSpeakerName.mock.calls).toEqual([["rec-1", "SPEAKER_00"]]);

    fireEvent.click(screen.getByRole("button", { name: "Not Alex" }));
    await waitFor(() => expect(onRefresh).toHaveBeenCalledTimes(2));
    expect(dismissSpeakerName.mock.calls.map(([, label]) => label).sort()).toEqual([
      "SPEAKER_00", "SPEAKER_03",
    ]);
  });

  it("confirms each pending member of every row with Confirm all", async () => {
    const grouped = groupedAlex();
    const pendingBob = buildSpeaker({
      diarization_label: "SPEAKER_01",
      local_name: "Bob",
      id: 2,
      name_source: "llm",
      name_pending: true,
    });
    const { onRefresh } = renderPanel({
      ...grouped,
      speakers: [...grouped.speakers, pendingBob],
      segments: [...grouped.segments, buildSegment({ speaker: "SPEAKER_01", start: 8, end: 9 })],
    });

    fireEvent.click(screen.getByRole("button", { name: "Confirm all (2)" }));

    await waitFor(() => expect(onRefresh).toHaveBeenCalledTimes(1));
    expect(confirmSpeakerName.mock.calls.map(([, label]) => label).sort()).toEqual([
      "SPEAKER_00",
      "SPEAKER_01",
      "SPEAKER_03",
    ]);
    expect(addNotification.mock.calls).toEqual([
      [{ type: "success", message: "Confirmed 2 names." }],
    ]);
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
