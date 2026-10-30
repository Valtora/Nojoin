import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ComponentProps } from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import TranscriptSection from "@/test/TranscriptSectionWithEdits";
import api from "@/lib/api/client";
import { getTranscriptUtterances } from "@/lib/api";
import { applyTranscriptDelta } from "@/lib/transcriptState";
import { RecordingStatus, type Recording, type TranscriptUtteranceList } from "@/types";

const ONE = "LIVE_PROVISIONAL_1";
const TWO = "LIVE_PROVISIONAL_2";
const THREE = "LIVE_PROVISIONAL_3";
const requests: Request[] = [];
const originalAdapter = api.defaults.adapter;
const originalScrollIntoView = Element.prototype.scrollIntoView;
let read: TranscriptUtteranceList;

beforeEach(() => {
  requests.length = 0;
  api.defaults.adapter = "fetch";
  vi.stubGlobal("fetch", vi.fn(async (request: Request) => {
    requests.push(request);
    const body = request.url.endsWith("/utterances") ? read : request.method === "GET" ? [] : {};
    return new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } });
  }));
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(() => {
  api.defaults.adapter = originalAdapter;
  Element.prototype.scrollIntoView = originalScrollIntoView;
  vi.unstubAllGlobals();
});

function props(delta: TranscriptUtteranceList): ComponentProps<typeof TranscriptSection> {
  const state = applyTranscriptDelta({ currentState: null, recordingId: "rec-1",
    fallbackSegments: [], delta, mode: "full" });
  return {
    active: true, recording: { id: "rec-1", status: RecordingStatus.PAUSED, speakers: [] } as unknown as Recording,
    timelineNotes: [], transcriptSegments: state.segments, currentTime: -1, isPlaying: false,
    speakerMap: { ...delta.live_speaker_names }, speakerColors: {}, globalSpeakers: [], canUndo: false, canRedo: false,
    deferredTranscriptUtteranceIds: [], onPlaySegment: vi.fn(), onPause: vi.fn(), onResume: vi.fn(),
    onRenameSpeaker: vi.fn(), onRefresh: vi.fn(), onUpdateSegmentSpeaker: vi.fn(), onUpdateSegmentText: vi.fn(),
    onFindAndReplace: vi.fn(), onUndo: vi.fn(), onRedo: vi.fn(), onExport: vi.fn(),
    onActiveEditUtteranceChange: vi.fn(), canPlayback: false, canExport: false,
    liveTranscript: { segments: state.segments, speakers: delta.speakers, hasLoaded: true,
      liveSpeakerNames: delta.live_speaker_names ?? {}, refresh: vi.fn(), applyLiveSpeakerNames: vi.fn() },
  };
}

it.each([false, true])("selects a distinct %s-named live identity through the menu and shared API action", async (named) => {
  read = {
    recording_id: "rec-1", revision: 9, settling_required: true, speakers: [], tombstones: [],
    live_speaker_names: { [ONE]: "Source", ...(named ? { [TWO]: "Alex", [THREE]: "Alex" } : {}) },
    utterances: [ONE, TWO, TWO, THREE].map((speaker, index) => ({
      id: `u${index}`, revision: 3, speaker, start: index * 4, end: index * 4 + 2,
      start_ms: index * 4000, end_ms: index * 4000 + 2000,
      text: `Voice ${index}.`, state: "provisional", provisional: true, segment_source: "live", locked: true,
    })),
  };
  render(<TranscriptSection {...props(await getTranscriptUtterances("rec-1"))} />);
  fireEvent.click(screen.getByRole("button", { name: "Actions for the line at 00:00" }));
  fireEvent.click(screen.getByRole("menuitem", { name: "Change speaker" }));
  const targetName = named ? "Alex Speaker 2" : "Speaker 2 Recording only";
  expect(screen.getAllByRole("button", { name: targetName })).toHaveLength(1);
  expect(screen.getAllByRole("button", { name: named ? "Alex Speaker 3" : "Speaker 3 Recording only" })).toHaveLength(1);
  expect(screen.queryByRole("button", { name: "Source Speaker 1" })).not.toBeInTheDocument();
  const search = screen.getByPlaceholderText("Search or add...");
  fireEvent.change(search, { target: { value: named ? " Alex " : " Speaker 2 " } });
  fireEvent.keyDown(search, { key: "Enter" });
  await Promise.resolve();
  // A typed display name cannot identify one of the live labels. The choice
  // retains its label even when two voices are both called Alex.
  expect(requests.filter((request) => request.method === "PATCH")).toHaveLength(0);
  fireEvent.click(screen.getByRole("button", { name: targetName }));
  await waitFor(() => expect(requests.filter((request) => request.method === "PATCH")).toHaveLength(1));
  const patch = requests.find((request) => request.method === "PATCH")!;
  expect(new URL(patch.url).pathname).toMatch(/\/transcripts\/rec-1\/utterances\/u0\/speaker$/);
  expect(await patch.json()).toEqual({ new_speaker_name: named ? "Alex" : "Speaker 2",
    diarization_label: TWO, scope: "utterance_only", expected_revision: 3 });
});

it("offers no new speaker for the name the line's own live label already has", async () => {
  read = {
    recording_id: "rec-1", revision: 9, settling_required: true, speakers: [], tombstones: [],
    live_speaker_names: { [ONE]: "Alex" },
    utterances: [ONE, TWO].map((speaker, index) => ({
      id: `u${index}`, revision: 3, speaker, start: index * 4, end: index * 4 + 2,
      start_ms: index * 4000, end_ms: index * 4000 + 2000,
      text: `Voice ${index}.`, state: "provisional", provisional: true, segment_source: "live", locked: true,
    })),
  };
  render(<TranscriptSection {...props(await getTranscriptUtterances("rec-1"))} />);
  fireEvent.click(screen.getByRole("button", { name: "Actions for the line at 00:00" }));
  fireEvent.click(screen.getByRole("menuitem", { name: "Change speaker" }));
  const search = screen.getByPlaceholderText("Search or add...");
  fireEvent.change(search, { target: { value: "alex" } });
  expect(screen.queryByText(/Create local/)).not.toBeInTheDocument();
  fireEvent.keyDown(search, { key: "Enter" });
  await Promise.resolve();
  expect(requests.filter((request) => request.method === "PATCH")).toHaveLength(0);
});

it("lists live speakers for this line only, never for the whole transcript", async () => {
  read = {
    recording_id: "rec-1", revision: 9, settling_required: true, speakers: [], tombstones: [],
    live_speaker_names: {},
    utterances: [ONE, TWO].map((speaker, index) => ({
      id: `u${index}`, revision: 3, speaker, start: index * 4, end: index * 4 + 2,
      start_ms: index * 4000, end_ms: index * 4000 + 2000,
      text: `Voice ${index}.`, state: "provisional", provisional: true, segment_source: "live", locked: true,
    })),
  };
  render(<TranscriptSection {...props(await getTranscriptUtterances("rec-1"))} />);
  fireEvent.click(screen.getByRole("button", { name: "Actions for the line at 00:00" }));
  fireEvent.click(screen.getByRole("menuitem", { name: "Change speaker" }));
  expect(screen.getByRole("button", { name: "Speaker 2 Recording only" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Whole transcript" }));
  expect(screen.queryByRole("button", { name: "Speaker 2 Recording only" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "This utterance" }));
  fireEvent.click(screen.getByRole("button", { name: "Speaker 2 Recording only" }));
  await waitFor(() => expect(requests.filter((request) => request.method === "PATCH")).toHaveLength(1));
  const patch = requests.find((request) => request.method === "PATCH")!;
  expect(await patch.json()).toMatchObject({ diarization_label: TWO, scope: "utterance_only" });
});

it.each([false, true])("offers no live speaker on a processed meeting that kept a live line (rerun feed: %s)",
  async (feed) => {
    read = {
      recording_id: "rec-1", revision: 9, settling_required: true, tombstones: [], live_speaker_names: {},
      speakers: [{ id: 1, diarization_label: "SPEAKER_00", name: "Alice", recording_id: "rec-1" }] as never,
      utterances: ["SPEAKER_00", TWO].map((speaker, index) => ({
        id: `u${index}`, revision: 3, speaker, start: index * 4, end: index * 4 + 2,
        start_ms: index * 4000, end_ms: index * 4000 + 2000,
        text: `Voice ${index}.`, state: "finalized", segment_source: index ? "live" : "final", locked: true,
      })),
    };
    const shown = props(await getTranscriptUtterances("rec-1"));
    const processed = { ...shown.recording, status: RecordingStatus.PROCESSED, client_status: "idle",
      speakers: read.speakers, transcript: { id: 1, recording_id: "rec-1", segments: [],
        transcript_status: "completed", notes_status: "completed" } } as unknown as Recording;
    render(<TranscriptSection {...shown} recording={processed} speakerMap={{ SPEAKER_00: "Alice" }}
      liveTranscript={feed ? shown.liveTranscript : undefined} />);
    expect(screen.queryByTestId("live-transcript-panel")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Actions for Alice's line at 00:00" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Change speaker" }));
    expect(screen.getByPlaceholderText("Search or add...")).toBeInTheDocument();
    expect(screen.queryByText("Recording only")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Speaker 2 Recording only" })).not.toBeInTheDocument();
  });
