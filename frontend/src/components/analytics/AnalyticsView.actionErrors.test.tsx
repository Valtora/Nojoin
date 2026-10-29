import { act, fireEvent, renderWithProviders, screen } from "@/test/renderWithProviders";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { RecordingAnalytics } from "@/types";

const api = vi.hoisted(() => ({ getRecordingAnalytics: vi.fn(), generateRecordingAnalytics: vi.fn(),
  generateRecordingAiAnalytics: vi.fn() }));
vi.mock("@/lib/api", () => api);

import AnalyticsView from "./AnalyticsView";

const analytics = (): RecordingAnalytics => ({
  recording_id: "rec-1", transcript_revision: 4,
  speakers: [{ speaker_key: "rs:1", name: "Dana", public_id: null, diarization_label: "SPEAKER_00",
    color: null, global_speaker_id: null, is_named: true }],
  metrics: { duration_ms: 30000, utterance_count: 1,
    talk_time: { "rs:1": { speech_ms: 30000, share_of_speech: 1, share_of_duration: 1 } },
    turn_structure: { "rs:1": { turn_count: 1, median_turn_ms: 30000, longest_turn_ms: 30000,
      longest_turn_start_ms: 0, excluded_short_turns: 0 } },
    turn_taking: { transitions: [], response_latency: {}, immediate_transitions: 0, lapse_transitions: 0 },
    timeline: { bucket_ms: 60000, buckets: [] }, silence: { speech_ms: 30000, silence_ms: 0, silence_share: 0 },
    unattributed: { utterance_count: 0, speech_ms: 0 },
    overlap: { overlapped_ms: 0, overlap_share: 0, overlapping_speech_present: false } },
  attribution_warning: null, delivery: null, delivery_status: "pending", delivery_error_message: null,
  delivery_stale: false, delivery_baselines: {}, audio_overlap: null, audio_overlap_status: "pending",
  audio_overlap_error_message: null,
  ai_status: "completed", ai_error_message: null, ai_stale: true,
  ai_budget: { limit: null, remaining: null, used: 0, resets_at: "2026-10-30T00:00:00Z" },
  ai: { method_version: 1, computed_at: "2026-10-29T10:00:00Z", event_watermark: 2,
    topics: [{ title: "Previous discussion", summary: "Original result", start_ms: 0, end_ms: 12000,
      led_by: null, contested: false, leadership_basis: null }], sentiment: [], questions: [], decisions: [],
    transcript_truncated: false, analysed_through_ms: 12000,
    excluded: { unknown_speaker_items: 0, uncited_sentiment: 0, uncited_decisions: 0, unverifiable_citations: 0,
      out_of_range_citations: 0, malformed_items: 0, ambiguous_speaker_names: 0 } },
});

describe("analytics output action failures", () => {
  beforeEach(() => {
    api.getRecordingAnalytics.mockReset().mockResolvedValue(analytics());
    api.generateRecordingAnalytics.mockReset();
    api.generateRecordingAiAnalytics.mockReset();
  });
  afterEach(() => vi.useRealTimers());

  it("retains all loaded outputs when a discussion regeneration is refused", async () => {
    api.generateRecordingAiAnalytics.mockRejectedValue({ response: { data: { detail: "Daily budget is used up" } } });
    renderWithProviders(<AnalyticsView recordingId="rec-1" />);
    fireEvent.click(await screen.findByRole("button", { name: "Analyse again" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Daily budget is used up");
    expect(screen.getByText("Previous discussion")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Who spoke" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Measure delivery" })).toBeEnabled();
  });

  it("scopes a delivery start failure without hiding successful discussion", async () => {
    api.generateRecordingAnalytics.mockRejectedValue({ response: { data: { detail: "Audio unavailable" } } });
    renderWithProviders(<AnalyticsView recordingId="rec-1" />);
    fireEvent.click(await screen.findByRole("button", { name: "Measure delivery" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Audio unavailable");
    expect(screen.getByText("Previous discussion")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Who spoke" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Analyse again" })).toBeEnabled();
  });

  it("keeps polling after an accepted discussion request whose immediate refresh fails", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    api.generateRecordingAiAnalytics.mockResolvedValue({ ai_status: "generating" });
    renderWithProviders(<AnalyticsView recordingId="rec-1" />);
    await screen.findByText("Previous discussion");
    api.getRecordingAnalytics.mockRejectedValueOnce(new Error("Refresh failed"));
    fireEvent.click(screen.getByRole("button", { name: "Analyse again" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Meeting analysis started");
    expect(screen.getByRole("heading", { name: "Who spoke" })).toBeInTheDocument();
    expect(screen.getByText(/Analysing the meeting/)).toBeInTheDocument();
    const completed = analytics();
    completed.ai!.topics[0].title = "Updated discussion";
    api.getRecordingAnalytics.mockResolvedValue(completed);
    await act(() => vi.advanceTimersByTimeAsync(3000));
    expect(screen.getByText("Updated discussion")).toBeInTheDocument();
    expect(api.getRecordingAnalytics).toHaveBeenCalledTimes(3);
  });
});
