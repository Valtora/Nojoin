import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ComponentProps } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import TranscriptSection from "@/test/TranscriptSectionWithEdits";
import SpeakerPanel from "../SpeakerPanel";
import api from "@/lib/api/client";
import { getTranscriptUtterances } from "@/lib/api";
import { applyTranscriptDelta } from "@/lib/transcriptState";
import { ClientStatus, RecordingStatus, type Recording, type TranscriptUtteranceList } from "@/types";
import { line, panelProps, person } from "./transcriptTestFixtures";

const ONE = "LIVE_PROVISIONAL_1";
const TWO = "LIVE_PROVISIONAL_2";
const THREE = "LIVE_PROVISIONAL_3";
const requests: Request[] = [];
const originalAdapter = api.defaults.adapter;
const originalScrollIntoView = Element.prototype.scrollIntoView;
let read: TranscriptUtteranceList;
const merged = { live_speaker_names: { [THREE]: "Speaker 1" }, live_speaker_merges: { [THREE]: ONE } };

beforeEach(() => {
  requests.length = 0;
  api.defaults.adapter = "fetch";
  vi.stubGlobal("fetch", vi.fn(async (request: Request) => {
    requests.push(request);
    const body = request.url.endsWith("/utterances") ? read : request.url.endsWith("/merge") ? merged
      : request.method === "GET" ? [] : {};
    return new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } });
  }));
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(() => {
  api.defaults.adapter = originalAdapter;
  Element.prototype.scrollIntoView = originalScrollIntoView;
  vi.unstubAllGlobals();
});

const capture = (labels: string[], settled = true): TranscriptUtteranceList => ({
  recording_id: "rec-1", revision: 9, settling_required: settled, speakers: [], tombstones: [],
  live_speaker_names: { [TWO]: "Bea Sample" },
  utterances: labels.map((speaker, index) => ({
    id: `u${index}`, revision: 3, speaker, start: index * 4, end: index * 4 + 2,
    start_ms: index * 4000, end_ms: index * 4000 + 2000,
    text: `Voice ${index}.`, state: "provisional", provisional: true, segment_source: "live",
    // A line still settling: merging moves its speaker, not its words.
    locked: index !== 2,
  })),
});

function sectionProps(delta: TranscriptUtteranceList, applyMerges = vi.fn()): ComponentProps<typeof TranscriptSection> {
  const state = applyTranscriptDelta({ currentState: null, recordingId: "rec-1",
    fallbackSegments: [], delta, mode: "full" });
  return {
    active: true,
    recording: { id: "rec-1", status: RecordingStatus.UPLOADING, client_status: ClientStatus.RECORDING,
      speakers: [] } as unknown as Recording,
    timelineNotes: [], transcriptSegments: state.segments, currentTime: -1, isPlaying: false,
    speakerMap: { ...delta.live_speaker_names }, speakerColors: {}, globalSpeakers: [person(9, "Robin Example")],
    canUndo: false, canRedo: false,
    deferredTranscriptUtteranceIds: [], onPlaySegment: vi.fn(), onPause: vi.fn(), onResume: vi.fn(),
    onRenameSpeaker: vi.fn(), onRefresh: vi.fn(), onUpdateSegmentSpeaker: vi.fn(), onUpdateSegmentText: vi.fn(),
    onFindAndReplace: vi.fn(), onUndo: vi.fn(), onRedo: vi.fn(), onExport: vi.fn(),
    onActiveEditUtteranceChange: vi.fn(), canPlayback: false, canExport: false,
    liveTranscript: { segments: state.segments, speakers: delta.speakers, hasLoaded: true,
      liveSpeakerNames: delta.live_speaker_names ?? {}, refresh: vi.fn(), applyLiveSpeakerNames: vi.fn(),
      applyLiveSpeakerMerges: applyMerges },
  };
}

const merges = () => requests.filter((request) => request.method === "POST" && request.url.endsWith("/merge"));

describe("merging live speakers from a line", () => {
  it("offers this capture's other live speakers alone, and merges the line's whole speaker", async () => {
    read = capture([THREE, ONE, THREE, TWO]);
    const applyMerges = vi.fn();
    render(<TranscriptSection {...sectionProps(await getTranscriptUtterances("rec-1"), applyMerges)} />);
    // The line still settling offers it too: a merge moves no word.
    fireEvent.click(screen.getByRole("button", { name: "Actions for the line at 00:08" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Merge speaker into..." }));

    expect(screen.getByText("Whole speaker in this recording")).toBeInTheDocument();
    const picker = within(screen.getByPlaceholderText("Search or add...").closest<HTMLElement>(".fixed")!);
    expect(picker.getByRole("button", { name: "Speaker 1 Recording only" })).toBeInTheDocument();
    expect(picker.getByRole("button", { name: "Bea Sample Speaker 2" })).toBeInTheDocument();
    expect(picker.queryByRole("button", { name: /Speaker 3/ })).not.toBeInTheDocument();
    // No People and nothing to create, typed or not.
    const search = screen.getByPlaceholderText("Search or add...");
    fireEvent.change(search, { target: { value: "Robin" } });
    expect(screen.queryByText("Robin Example")).not.toBeInTheDocument();
    expect(screen.queryByText(/Create local/)).not.toBeInTheDocument();
    fireEvent.change(search, { target: { value: "" } });

    fireEvent.click(screen.getByRole("button", { name: "Speaker 1 Recording only" }));
    await waitFor(() => expect(merges()).toHaveLength(1));
    expect(new URL(merges()[0].url).pathname).toMatch(/\/speakers\/recordings\/rec-1\/live\/LIVE_PROVISIONAL_3\/merge$/);
    expect(await merges()[0].json()).toEqual({ into_label: ONE });
    await waitFor(() => expect(applyMerges).toHaveBeenCalledWith({ [THREE]: ONE }));
  });

  it("is not offered with no other live speaker", async () => {
    read = capture([ONE, ONE]);
    render(<TranscriptSection {...sectionProps(await getTranscriptUtterances("rec-1"))} />);
    fireEvent.click(screen.getByRole("button", { name: "Actions for the line at 00:00" }));
    expect(screen.queryByRole("menuitem", { name: "Merge speaker into..." })).not.toBeInTheDocument();
  });
});

describe("merging live speakers from the Speakers panel", () => {
  const props = (onMerge = vi.fn().mockResolvedValue(true)) => ({
    ...panelProps(), speakers: [],
    segments: [ONE, TWO, THREE].map((speaker) => line({ speaker, settling_required: true, locked: true })),
    liveSpeakerNames: { [TWO]: "Bea Sample" }, onRenameLiveSpeaker: vi.fn().mockResolvedValue(true),
    onMergeLiveSpeaker: onMerge, globalSpeakers: [person(9, "Robin Example")],
  });
  const menuItem = (speakerName: string, item: string) => {
    fireEvent.click(screen.getByRole("button", { name: `Actions for ${speakerName}` }));
    return within(screen.getByRole("menu", { name: `Actions for ${speakerName}` }))
      .getByRole("menuitem", { name: item });
  };

  it("merges a speaker into another live speaker from Merge into", async () => {
    const onMerge = vi.fn().mockResolvedValue(true);
    render(<SpeakerPanel {...props(onMerge)} />);
    fireEvent.click(menuItem("Speaker 3", "Merge into..."));

    expect(screen.queryByText("Robin Example")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Speaker 1 Recording only" }));
    await waitFor(() => expect(onMerge).toHaveBeenCalledWith(THREE, ONE));
  });

  it("merges when Change identity picks another live speaker, and renames for a name", async () => {
    const options = props();
    render(<SpeakerPanel {...options} />);
    fireEvent.click(menuItem("Speaker 3", "Change identity"));
    fireEvent.click(screen.getByRole("button", { name: "Bea Sample Speaker 2" }));
    await waitFor(() => expect(options.onMergeLiveSpeaker).toHaveBeenCalledWith(THREE, TWO));
    expect(options.onRenameLiveSpeaker).not.toHaveBeenCalled();

    fireEvent.click(menuItem("Speaker 3", "Change identity"));
    const search = screen.getByPlaceholderText("Search or add...");
    fireEvent.change(search, { target: { value: "Dana Sample" } });
    fireEvent.keyDown(search, { key: "Enter" });
    await waitFor(() => expect(options.onRenameLiveSpeaker).toHaveBeenCalledWith(THREE, "Dana Sample", false));
  });

  it("offers no merge with a single live speaker", () => {
    render(<SpeakerPanel {...props()} segments={[line({ speaker: ONE, settling_required: true, locked: true })]} />);
    fireEvent.click(screen.getByRole("button", { name: "Actions for Speaker 1" }));
    expect(screen.queryByRole("menuitem", { name: "Merge into..." })).not.toBeInTheDocument();
  });
});
