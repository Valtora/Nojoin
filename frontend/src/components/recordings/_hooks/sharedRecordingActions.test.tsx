import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

import { describe, expect, it } from "vitest";
import { vi } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ComponentProps } from "react";
import TranscriptSection from "@/test/TranscriptSectionWithEdits";
import { ClientStatus, RecordingStatus, type Recording, type TranscriptSegment } from "@/types";

import { RECORDING_ACTION_IDS } from "./useRecordingActions";

/**
 * Invariant: every surface that offers recording actions drives them through
 * the SAME shared action model, so the duplication this hook was extracted to
 * remove cannot silently reappear.
 *
 * This used to name Sidebar and RecordingCard. RecordingCard was deleted: it
 * was written for a recordings grid view that no route ever rendered, in the
 * entire history of the repository. The invariant itself is real and survives
 * with `Sidebar` and the recording detail surfaces. The header slot now owns
 * discard; real line-menu commands below exercise the shared line actions.
 */

const here = dirname(fileURLToPath(import.meta.url));
const componentsDir = resolve(here, "..", "..");

const readComponent = (name: string): string =>
  readFileSync(resolve(componentsDir, name), "utf8");

const sidebarSource = readComponent("Sidebar.tsx");
const captureSlotSource = readComponent("RecordingCaptureSlot.tsx");

const usedActionIds = (source: string): string[] =>
  RECORDING_ACTION_IDS.filter((id) =>
    new RegExp(`actions\\.${id}\\b`).test(source),
  );

describe("shared recording action model", () => {
  it("Sidebar consumes the shared useRecordingActions hook", () => {
    expect(sidebarSource).toContain("useRecordingActions");
  });

  it("the rail and header discard draw their actions from the same shared set", () => {
    const sidebarActions = usedActionIds(sidebarSource);
    const statusActions = usedActionIds(captureSlotSource);

    // Both surfaces must actually use the shared hook's actions...
    expect(sidebarActions.length).toBeGreaterThan(0);
    expect(statusActions.length).toBeGreaterThan(0);

    // ...and every action they use must be a member of the single shared set,
    // so neither surface can introduce a divergent, locally-defined action.
    for (const id of [...sidebarActions, ...statusActions]) {
      expect(RECORDING_ACTION_IDS).toContain(id);
    }

    // Discard is the action both surfaces offer, and the one whose behaviour
    // has to match: the live view and the rail must dispose of a recording the
    // same way.
    expect(sidebarActions).toContain("discard");
    expect(statusActions).toContain("discard");
  });
});

const api = vi.hoisted(() => ({ edit: vi.fn(), assign: vi.fn(), split: vi.fn() }));
vi.mock("@/lib/api", async (original) => ({
  ...await original<typeof import("@/lib/api")>(),
  updateTranscriptUtteranceText: api.edit, updateTranscriptUtteranceSpeaker: api.assign,
  getGlobalSpeakers: vi.fn().mockResolvedValue([]),
}));
vi.mock("@/lib/api/transcriptSplit", () => ({ splitTranscriptUtterance: api.split }));

const line: TranscriptSegment = {
  id: "u1", revision: 3, start: 0, end: 2, speaker: "SPEAKER_0", text: "Alpha beta",
  settling_required: true, locked: true,
  words: [
    { id: "w1", order: 0, word: "Alpha", start_ms: 0, end_ms: 500, locked: true },
    { id: "w2", order: 1, word: "beta", start_ms: 700, end_ms: 1600, locked: true },
  ],
};
const sectionProps = (): ComponentProps<typeof TranscriptSection> => ({
  active: true, recording: { id: "rec-1", status: RecordingStatus.PAUSED, speakers: [] } as unknown as Recording,
  timelineNotes: [], transcriptSegments: [line], currentTime: -1, isPlaying: false,
  speakerMap: {}, speakerColors: {}, globalSpeakers: [], canUndo: false, canRedo: false,
  deferredTranscriptUtteranceIds: [], onPlaySegment: vi.fn(), onPause: vi.fn(), onResume: vi.fn(),
  onRenameSpeaker: vi.fn(), onRefresh: vi.fn(), onUpdateSegmentSpeaker: vi.fn(), onUpdateSegmentText: vi.fn(),
  onFindAndReplace: vi.fn(), onUndo: vi.fn(), onRedo: vi.fn(), onExport: vi.fn(),
  onActiveEditUtteranceChange: vi.fn(), canPlayback: false, canExport: false,
  liveTranscript: {
    segments: [line], speakers: [], hasLoaded: true, liveSpeakerNames: {},
    refresh: vi.fn(), applyLiveSpeakerNames: vi.fn(),
  },
});
const commands = [
  { menu: "Edit text", api: "edit", payload: "My correction" },
  { menu: "Change speaker", api: "assign", payload: { name: "Alex", scope: "utterance_only" } },
  { menu: "Split line", api: "split", payload: "w2" },
] as const;
const openCommand = (menu: string) => {
  fireEvent.click(screen.getByRole("button", { name: /Actions for .*line at 00:00/ }));
  fireEvent.click(screen.getByRole("menuitem", { name: menu }));
  if (menu === "Edit text") fireEvent.change(screen.getByRole("textbox", { name: "Line text" }),
    { target: { value: "My correction" } });
  if (menu === "Change speaker") fireEvent.change(screen.getByPlaceholderText("Search or add..."),
    { target: { value: "Alex" } });
};
const submitCommand = (menu: string) => {
  if (menu === "Change speaker") fireEvent.keyDown(screen.getByPlaceholderText("Search or add..."), { key: "Enter" });
  else fireEvent.click(screen.getByRole("button", { name: menu === "Split line" ? "Split" : "Save" }));
};
const expectDraft = (menu: string) => {
  if (menu === "Edit text") expect(screen.getByRole("textbox", { name: "Line text" })).toHaveValue("My correction");
  if (menu === "Change speaker") expect(screen.getByPlaceholderText("Search or add...")).toHaveValue("Alex");
  if (menu === "Split line") expect(screen.getByRole("combobox", { name: "Split before word" })).toHaveValue("w2");
};

describe("shared recording line commands", () => {
  it.each(commands)("runs $menu with the opened revision, retains refusal, and refreshes the current reader", async (command) => {
    const mutation = api[command.api].mockReset()
      .mockRejectedValueOnce({ response: { status: 409, data: { detail: "Stale revision" } } })
      .mockResolvedValueOnce({});
    const props = sectionProps();
    const view = render(<TranscriptSection {...props} />);
    openCommand(command.menu);
    view.rerender(<TranscriptSection {...props} liveTranscript={undefined}
      recording={{ ...props.recording, status: RecordingStatus.PROCESSED }}
      transcriptSegments={[{ ...line, revision: 9 }]} />);
    submitCommand(command.menu);
    await waitFor(() => expect(mutation).toHaveBeenCalledTimes(1));
    expect(mutation).toHaveBeenLastCalledWith("rec-1", "u1", command.payload, 3);
    expectDraft(command.menu);
    expect(props.onRefresh).not.toHaveBeenCalled();
    expect(props.liveTranscript!.refresh).not.toHaveBeenCalled();
    // Let the refused request leave the editor's saving state before retrying.
    await act(async () => {});
    submitCommand(command.menu);
    await waitFor(() => expect(props.onRefresh).toHaveBeenCalledTimes(1));
    expect(mutation).toHaveBeenCalledTimes(2);
    expect(mutation).toHaveBeenLastCalledWith("rec-1", "u1", command.payload, 3);
    expect(props.liveTranscript!.refresh).not.toHaveBeenCalled();
    expect(props.onUpdateSegmentText).not.toHaveBeenCalled();
    expect(props.onUpdateSegmentSpeaker).not.toHaveBeenCalled();
    expect(screen.queryByRole("textbox", { name: "Line text" })).not.toBeInTheDocument();
    expect(screen.queryByPlaceholderText("Search or add...")).not.toBeInTheDocument();
    expect(screen.queryByRole("combobox", { name: "Split before word" })).not.toBeInTheDocument();
  });

  it.each([RecordingStatus.QUEUED, RecordingStatus.PROCESSING])("offers no replacement commands during %s reprocessing", (status) => {
    const props = sectionProps();
    const view = render(<TranscriptSection {...props} liveTranscript={undefined}
      recording={{ ...props.recording, status: RecordingStatus.PROCESSED }} />);
    view.rerender(<TranscriptSection {...props}
      recording={{ ...props.recording, status, client_status: ClientStatus.IDLE }} />);
    // This must exercise preserved, locked content, rather than an empty result.
    expect(document.querySelector("p[data-segment-id]")).toHaveTextContent("Alpha beta");
    const trigger = screen.queryByRole("button", { name: /Actions for .*line at 00:00/ });
    if (trigger) fireEvent.click(trigger);
    for (const { menu } of commands) expect(screen.queryByRole("menuitem", { name: menu })).not.toBeInTheDocument();
  });

  it.each(commands)("keeps an open $menu draft local during processing, then retries its captured revision", async (command) => {
    const mutation = api[command.api].mockReset().mockResolvedValue({});
    const props = sectionProps();
    const view = render(<TranscriptSection {...props} />);
    openCommand(command.menu);
    view.rerender(<TranscriptSection {...props}
      recording={{ ...props.recording, status: RecordingStatus.PROCESSING, client_status: ClientStatus.IDLE }} />);
    await act(async () => submitCommand(command.menu));
    expect(mutation).not.toHaveBeenCalled();
    expectDraft(command.menu);
    expect(props.liveTranscript!.refresh).not.toHaveBeenCalled();
    view.rerender(<TranscriptSection {...props} liveTranscript={undefined}
      recording={{ ...props.recording, status: RecordingStatus.PROCESSED }}
      transcriptSegments={[{ ...line, revision: 9 }]} />);
    submitCommand(command.menu);
    await waitFor(() => expect(mutation).toHaveBeenCalledWith("rec-1", "u1", command.payload, 3));
    expect(props.onRefresh).toHaveBeenCalledTimes(1);
  });

  it("keeps a finished text draft local when reprocessing begins", async () => {
    const props = sectionProps();
    const finished = { ...props.recording, status: RecordingStatus.PROCESSED };
    const view = render(<TranscriptSection {...props} liveTranscript={undefined} recording={finished} />);
    fireEvent.click(screen.getByRole("button", { name: /Actions for .*line at 00:00/ }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Edit text" }));
    const editor = screen.getByRole("textbox");
    fireEvent.change(editor, { target: { value: "Keep my finished draft" } });
    view.rerender(<TranscriptSection {...props}
      recording={{ ...finished, status: RecordingStatus.PROCESSING, client_status: ClientStatus.IDLE }} />);
    await act(async () => {
      fireEvent.keyDown(editor, { key: "Enter" });
      fireEvent.blur(editor);
    });
    expect(props.onUpdateSegmentText).not.toHaveBeenCalled();
    expect(editor).toHaveValue("Keep my finished draft");
    view.rerender(<TranscriptSection {...props} liveTranscript={undefined} recording={finished}
      transcriptSegments={[{ ...line, revision: 9 }]} />);
    await act(async () => fireEvent.keyDown(editor, { key: "Enter" }));
    expect(props.onUpdateSegmentText).toHaveBeenCalledWith(line, "Keep my finished draft");
  });

  it("keeps a finished speaker draft local when reprocessing begins", async () => {
    const props = sectionProps();
    props.onUpdateSegmentSpeaker = vi.fn().mockResolvedValue(false);
    const finished = { ...props.recording, status: RecordingStatus.PROCESSED };
    const view = render(<TranscriptSection {...props} liveTranscript={undefined} recording={finished} />);
    openCommand("Change speaker");
    const editor = screen.getByPlaceholderText("Search or add...");
    view.rerender(<TranscriptSection {...props}
      recording={{ ...finished, status: RecordingStatus.PROCESSING, client_status: ClientStatus.IDLE }} />);
    await act(async () => submitCommand("Change speaker"));
    expect(props.onUpdateSegmentSpeaker).not.toHaveBeenCalled();
    expect(editor).toHaveValue("Alex");
    view.rerender(<TranscriptSection {...props} liveTranscript={undefined} recording={finished}
      transcriptSegments={[{ ...line, revision: 9 }]} />);
    await act(async () => submitCommand("Change speaker"));
    expect(props.onUpdateSegmentSpeaker).toHaveBeenCalledWith(line, { name: "Alex", scope: "utterance_only" });
  });
});
