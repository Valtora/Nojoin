import { act, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { ViewportDensityProvider } from "@/components/ViewportDensityProvider";
import { CaptureProvider, useCapture } from "@/lib/capture/CaptureProvider";
import { clearPausedCaptureContext, writePausedCaptureContext } from "@/lib/capture/shared";
import { forgetNoteOutboxes } from "@/lib/notes/noteOutboxes";
import { ClientStatus, RecordingStatus, type Recording } from "@/types";

import { useRecordingDetail } from "./useRecordingDetail";

// The real provider, controller, detail and transcript hooks; the network and
// the router are the edges.
const api = vi.hoisted(() => ({
  router: { push: vi.fn(), refresh: vi.fn() },
  getRecording: vi.fn(), getUnfinishedCaptures: vi.fn(), getRecordingIfChanged: vi.fn(),
  getSettings: vi.fn(async () => ({})), getGlobalSpeakers: vi.fn(async () => []),
  listTimelineNotes: vi.fn(async () => []),
  getTranscriptUtterances: vi.fn(async () => ({
    recording_id: "rec-1", revision: 1, speakers: [], tombstones: [], utterances: [],
  })),
}));
vi.mock("@/lib/api", async (original) => ({ ...await original<typeof import("@/lib/api")>(), ...api }));
vi.mock("@/lib/api/transcript", async (original) => ({
  ...await original<typeof import("@/lib/api/transcript")>(), getTranscriptUtterances: api.getTranscriptUtterances,
}));
vi.mock("next/navigation", () => ({
  useRouter: () => api.router,
  usePathname: () => "/recordings/rec-1",
  useSearchParams: () => new URLSearchParams(),
}));

const params = Promise.resolve({ id: "rec-1" });
const finished: Recording = {
  id: "rec-1", name: "Finished elsewhere", meeting_uid: "meeting-1", audio_path: "/data/rec-1.wav",
  is_archived: false, is_deleted: false, status: RecordingStatus.PROCESSED,
  client_status: ClientStatus.IDLE, capture_epoch: 4, has_audio: true, has_proxy: true,
  duration_seconds: 60, speakers: [], created_at: "2026-10-29T12:00:00Z", updated_at: "2026-10-29T12:01:00Z",
};

function Capabilities() {
  const capture = useCapture();
  const detail = useRecordingDetail({ params, capture });
  return <output data-testid="state">{JSON.stringify({
    loading: detail.loading, stored: capture.recordingId, owned: capture.ownedRecordingId,
    localCaptureActive: detail.localCaptureActive, canPlayback: detail.canPlayback, canExport: detail.canExport,
    usesInFlightTranscript: detail.usesInFlightTranscript,
  })}</output>;
}

const readState = () => JSON.parse(screen.getByTestId("state").textContent ?? "{}");

beforeEach(() => {
  clearPausedCaptureContext();
  api.getRecording.mockResolvedValue(finished);
  api.getUnfinishedCaptures.mockResolvedValue([]);
  api.getRecordingIfChanged.mockResolvedValue(null);
});
afterEach(() => {
  clearPausedCaptureContext();
  forgetNoteOutboxes();
});

it("plays and exports a recording finished on another device in the tab that captured it before a reload", async () => {
  // What the controller stores on pagehide; the capture was then resumed and
  // finished elsewhere, so this tab has neither a runtime nor an offer.
  writePausedCaptureContext({ recordingId: "rec-1", lastSequence: 10, epoch: 3, persistedAt: Date.now() });
  render(<ViewportDensityProvider><CaptureProvider><Capabilities /></CaptureProvider></ViewportDensityProvider>);
  await act(async () => {});
  await waitFor(() => expect(readState().loading).toBe(false));

  const state = readState();
  expect(state.stored).toBe("rec-1");
  expect(state.owned).toBeNull();
  expect(state.localCaptureActive).toBe(false);
  expect(state.canPlayback).toBe(true);
  expect(state.canExport).toBe(true);
  expect(state.usesInFlightTranscript).toBe(false);
});
