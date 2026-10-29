import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

import { describe, expect, it } from "vitest";
import { vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
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
const commands = ["Edit text", "Change speaker", "Split line"];

describe("shared recording line commands", () => {
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
    for (const menu of commands) expect(screen.queryByRole("menuitem", { name: menu })).not.toBeInTheDocument();
  });
});
