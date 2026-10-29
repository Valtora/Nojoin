import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, renderWithProviders, screen, waitFor } from "@/test/renderWithProviders";
import type { LiveRecordingAnalytics, RecordingAnalytics } from "@/types";

const getRecordingAnalytics = vi.fn<(...args: unknown[]) => Promise<RecordingAnalytics>>();
const getLiveRecordingAnalytics = vi.fn();
const getRecordingAiAnalytics = vi.fn();
vi.mock("@/lib/api", () => ({
  getRecordingAnalytics: (...args: unknown[]) => getRecordingAnalytics(...args),
  getLiveRecordingAnalytics: (...args: unknown[]) => getLiveRecordingAnalytics(...args),
  getRecordingAiAnalytics: (...args: unknown[]) => getRecordingAiAnalytics(...args),
  generateRecordingAnalytics: vi.fn(),
  generateRecordingAiAnalytics: vi.fn(),
}));

import AnalyticsSection from "./AnalyticsSection";

const live = (name = "Alex"): LiveRecordingAnalytics => ({
  recording_id: "rec-1", provisional: true, change_sequence: 2,
  speakers: [{ speaker_key: "live:1", name, public_id: null, diarization_label: "LIVE_1",
    color: null, global_speaker_id: null, is_named: true }],
  metrics: { utterance_count: 5,
    talk_time: { "live:1": { speech_ms: 30000, share_of_speech: 1, share_of_duration: 1 } },
    turn_structure: { "live:1": { turn_count: 2, median_turn_ms: 15000,
      longest_turn_ms: 20000, longest_turn_start_ms: 1000, excluded_short_turns: 0 } } },
  transcript_wpm: { "live:1": { words_per_minute: 180, sample_count: 5,
    excluded_short: 0, excluded_overlapping: 0 } },
});

const finished = (): RecordingAnalytics => ({
  recording_id: "rec-1", transcript_revision: 3, speakers: live("Final Alex").speakers,
  metrics: { ...live().metrics, duration_ms: 30_000,
    turn_taking: { transitions: [], response_latency: {}, immediate_transitions: 0, lapse_transitions: 0 },
    timeline: { bucket_ms: 60_000, buckets: [] },
    silence: { speech_ms: 30_000, silence_ms: 0, silence_share: 0 },
    unattributed: { utterance_count: 0, speech_ms: 0 },
    overlap: { overlapped_ms: 0, overlap_share: 0, overlapping_speech_present: false } },
  attribution_warning: null, delivery: null, delivery_status: "pending",
  delivery_error_message: null, delivery_stale: false,
  ai: null, ai_status: "pending", ai_error_message: null, ai_stale: false,
  ai_budget: { limit: null, used: 0, remaining: null, resets_at: "2026-10-12T00:00:00+00:00" },
  audio_overlap: null, audio_overlap_status: "pending", audio_overlap_error_message: null,
  delivery_baselines: {},
});

describe("AnalyticsSection live lifecycle", () => {
  beforeEach(() => {
    getRecordingAnalytics.mockReset();
    getLiveRecordingAnalytics.mockReset();
    getRecordingAnalytics.mockImplementation(() => new Promise(() => {}));
    getLiveRecordingAnalytics.mockResolvedValue(live());
    getRecordingAiAnalytics.mockResolvedValue(finished());
  });
  afterEach(() => vi.useRealTimers());

  it("retains estimates after completion until the delayed final response succeeds", async () => {
    let resolveFinal!: (value: RecordingAnalytics) => void;
    getRecordingAnalytics.mockImplementationOnce(() => new Promise((resolve) => { resolveFinal = resolve; }));
    const view = renderWithProviders(
      <AnalyticsSection active={false} inFlight recordingId="rec-1" speakerColors={{}} />,
    );
    expect(getLiveRecordingAnalytics).not.toHaveBeenCalled();
    view.rerender(<AnalyticsSection active inFlight recordingId="rec-1" speakerColors={{}} />);
    expect(await screen.findByText("Alex")).toBeInTheDocument();
    expect(getRecordingAnalytics).not.toHaveBeenCalled();
    expect(screen.getByText(/transcript estimates/i)).toBeInTheDocument();
    expect(screen.getByText("180")).toBeInTheDocument();
    expect(screen.getByText("20s")).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "Analyse meeting" })).toBeInTheDocument();
    expect(screen.getAllByText("After processing")).toHaveLength(3);
    view.rerender(<AnalyticsSection active recordingId="rec-1" speakerColors={{}} />);
    // The final view is lazy: its chunk must retain the same snapshot too.
    expect(screen.getByRole("cell", { name: "Alex" })).toBeVisible();
    await waitFor(() => expect(getRecordingAnalytics).toHaveBeenCalledWith("rec-1"));
    expect(screen.getByText("Alex")).toBeInTheDocument();
    expect(screen.getByText(/transcript estimates/i)).toBeInTheDocument();
    await act(async () => { resolveFinal(finished()); });
    expect((await screen.findAllByText("Final Alex")).length).toBeGreaterThan(0);
    expect(screen.queryByText("Alex")).not.toBeInTheDocument();
    expect(screen.queryByText(/transcript estimates/i)).not.toBeInTheDocument();
  });

  it("retains estimates through a failed final read and a pending retry", async () => {
    let resolveRetry!: (value: RecordingAnalytics) => void;
    getRecordingAnalytics.mockRejectedValueOnce(new Error("unavailable"));
    getRecordingAnalytics.mockImplementationOnce(() => new Promise((resolve) => { resolveRetry = resolve; }));
    const view = renderWithProviders(<AnalyticsSection active inFlight recordingId="rec-1" speakerColors={{}} />);
    await screen.findByText("Alex");
    view.rerender(<AnalyticsSection active recordingId="rec-1" speakerColors={{}} />);
    expect(await screen.findByText("Analytics could not be loaded for this meeting.")).toBeInTheDocument();
    expect(screen.getByText("Alex")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(getRecordingAnalytics).toHaveBeenCalledTimes(2);
    expect(screen.getByText("Alex")).toBeInTheDocument();
    await act(async () => { resolveRetry(finished()); });
    expect((await screen.findAllByText("Final Alex")).length).toBeGreaterThan(0);
    expect(screen.queryByText(/transcript estimates/i)).not.toBeInTheDocument();
  });

  it("accepts an empty final response and does not restore estimates when reopening the tab", async () => {
    getRecordingAnalytics.mockResolvedValueOnce({ ...finished(), speakers: [] });
    const view = renderWithProviders(<AnalyticsSection active inFlight recordingId="rec-1" speakerColors={{}} />);
    await screen.findByText("Alex");
    view.rerender(<AnalyticsSection active recordingId="rec-1" speakerColors={{}} />);
    expect(await screen.findByText("No analytics yet")).toBeInTheDocument();
    expect(screen.queryByText("Alex")).not.toBeInTheDocument();
    view.rerender(<AnalyticsSection active={false} recordingId="rec-1" speakerColors={{}} />);
    view.rerender(<AnalyticsSection active recordingId="rec-1" speakerColors={{}} />);
    await waitFor(() => expect(getRecordingAnalytics).toHaveBeenCalledTimes(2));
    expect(screen.getByText("Loading analytics...")).toBeInTheDocument();
    expect(screen.queryByText(/transcript estimates/i)).not.toBeInTheDocument();
  });

  it("stops live polling at completion and retains the snapshot across tab switches", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const view = renderWithProviders(<AnalyticsSection active inFlight recordingId="rec-1" speakerColors={{}} />);
    await screen.findByText("Alex");
    const liveReads = getLiveRecordingAnalytics.mock.calls.length;
    view.rerender(<AnalyticsSection active recordingId="rec-1" speakerColors={{}} />);
    await waitFor(() => expect(getRecordingAnalytics).toHaveBeenCalledTimes(1));
    await act(async () => { await vi.advanceTimersByTimeAsync(9000); });
    expect(getLiveRecordingAnalytics).toHaveBeenCalledTimes(liveReads);
    expect(screen.getByText("Alex")).toBeInTheDocument();
    view.rerender(<AnalyticsSection active={false} recordingId="rec-1" speakerColors={{}} />);
    view.rerender(<AnalyticsSection active recordingId="rec-1" speakerColors={{}} />);
    expect(screen.getByText("Alex")).toBeInTheDocument();
    await waitFor(() => expect(getRecordingAnalytics).toHaveBeenCalledTimes(2));
    expect(getLiveRecordingAnalytics).toHaveBeenCalledTimes(liveReads);
  });

  it("clears estimates on recording changes and ignores the previous final response", async () => {
    let resolveOldFinal!: (value: RecordingAnalytics) => void;
    getRecordingAnalytics.mockImplementationOnce(() => new Promise((resolve) => { resolveOldFinal = resolve; }));
    const view = renderWithProviders(<AnalyticsSection active inFlight recordingId="rec-1" speakerColors={{}} />);
    await screen.findByText("Alex");
    view.rerender(<AnalyticsSection active recordingId="rec-1" speakerColors={{}} />);
    await waitFor(() => expect(getRecordingAnalytics).toHaveBeenCalledTimes(1));
    let resolveNewLive!: (value: LiveRecordingAnalytics) => void;
    getLiveRecordingAnalytics.mockImplementationOnce(() => new Promise((resolve) => { resolveNewLive = resolve; }));
    view.rerender(<AnalyticsSection active inFlight recordingId="rec-2" speakerColors={{}} />);
    expect(screen.queryByText("Alex")).not.toBeInTheDocument();
    await act(async () => { resolveNewLive({ ...live("Blair"), recording_id: "rec-2" }); });
    expect(await screen.findByText("Blair")).toBeInTheDocument();
    await act(async () => { resolveOldFinal(finished()); });
    view.rerender(<AnalyticsSection active recordingId="rec-2" speakerColors={{}} />);
    await waitFor(() => expect(getRecordingAnalytics).toHaveBeenCalledTimes(2));
    expect(screen.getByText("Blair")).toBeInTheDocument();
    expect(screen.queryByText("Final Alex")).not.toBeInTheDocument();
  });

  it("keeps normal loading and error behavior for an already finished recording", async () => {
    let rejectFinal!: (reason: Error) => void;
    getRecordingAnalytics.mockImplementationOnce(() => new Promise((_, reject) => { rejectFinal = reject; }));
    renderWithProviders(<AnalyticsSection active recordingId="rec-1" speakerColors={{}} />);
    expect(await screen.findByText("Loading analytics...")).toBeInTheDocument();
    await waitFor(() => expect(getRecordingAnalytics).toHaveBeenCalledTimes(1));
    await act(async () => { rejectFinal(new Error("unavailable")); });
    expect(await screen.findByRole("button", { name: "Try again" })).toBeInTheDocument();
    expect(screen.queryByText(/transcript estimates/i)).not.toBeInTheDocument();
    expect(getLiveRecordingAnalytics).not.toHaveBeenCalled();
  });

  it("ignores a final response when the same recording enters a new processing pass", async () => {
    let resolveOldFinal!: (value: RecordingAnalytics) => void;
    getRecordingAnalytics.mockImplementationOnce(() => new Promise((resolve) => { resolveOldFinal = resolve; }));
    const view = renderWithProviders(<AnalyticsSection active inFlight recordingId="rec-1" speakerColors={{}} />);
    await screen.findByText("Alex");
    view.rerender(<AnalyticsSection active recordingId="rec-1" speakerColors={{}} />);
    await waitFor(() => expect(getRecordingAnalytics).toHaveBeenCalledTimes(1));
    getLiveRecordingAnalytics.mockResolvedValue(live("New pass"));
    view.rerender(<AnalyticsSection active inFlight recordingId="rec-1" speakerColors={{}} />);
    await screen.findByText("New pass");
    await act(async () => { resolveOldFinal(finished()); });
    view.rerender(<AnalyticsSection active recordingId="rec-1" speakerColors={{}} />);
    await waitFor(() => expect(getRecordingAnalytics).toHaveBeenCalledTimes(2));
    expect(screen.getByText("New pass")).toBeInTheDocument();
    expect(screen.queryByText("Final Alex")).not.toBeInTheDocument();
  });

  it("polls unchanged live data every three seconds and stops when inactive", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const view = renderWithProviders(<AnalyticsSection active inFlight recordingId="rec-1" speakerColors={{}} />);
    await screen.findByText("Alex");
    const initial = getLiveRecordingAnalytics.mock.calls.length;
    await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
    expect(getLiveRecordingAnalytics).toHaveBeenCalledTimes(initial + 1);
    view.rerender(<AnalyticsSection active={false} inFlight recordingId="rec-1" speakerColors={{}} />);
    await act(async () => { await vi.advanceTimersByTimeAsync(6000); });
    expect(getLiveRecordingAnalytics).toHaveBeenCalledTimes(initial + 1);
  });

  it("gives up on a live read that never answers and polls again", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    getLiveRecordingAnalytics.mockImplementationOnce((_id: string, signal: AbortSignal) =>
      new Promise((_resolve, reject) => signal.addEventListener("abort", () => reject(signal.reason))));
    renderWithProviders(<AnalyticsSection active inFlight recordingId="rec-1" speakerColors={{}} />);
    await act(async () => { await vi.advanceTimersByTimeAsync(12000); });
    expect(getLiveRecordingAnalytics).toHaveBeenCalledTimes(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(6000); });
    expect(await screen.findByText("Alex")).toBeInTheDocument();
  });

  it("ignores a live response that lands after completion", async () => {
    let resolvePoll!: (value: LiveRecordingAnalytics) => void;
    getLiveRecordingAnalytics.mockImplementationOnce(() => new Promise((resolve) => { resolvePoll = resolve; }));
    const view = renderWithProviders(<AnalyticsSection active inFlight recordingId="rec-1" speakerColors={{}} />);
    await waitFor(() => expect(getLiveRecordingAnalytics).toHaveBeenCalledTimes(1));
    view.rerender(<AnalyticsSection active recordingId="rec-1" speakerColors={{}} />);
    await waitFor(() => expect(getRecordingAnalytics).toHaveBeenCalledTimes(1));
    await act(async () => { resolvePoll(live("Late live")); });
    expect(screen.queryByText("Late live")).not.toBeInTheDocument();
  });

  it("keeps empty and undersampled results provisional without reporting a zero pace", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const estimates = live();
    estimates.transcript_wpm["live:1"].words_per_minute = null;
    getLiveRecordingAnalytics.mockResolvedValue(estimates);
    renderWithProviders(<AnalyticsSection active inFlight recordingId="rec-1" speakerColors={{}} />);
    expect(await screen.findByText("Not enough speech yet")).toBeInTheDocument();
    getLiveRecordingAnalytics.mockResolvedValue({ ...live(), speakers: [], metrics: {
      utterance_count: 0, talk_time: {}, turn_structure: {},
    }, transcript_wpm: {} });
    await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
    expect(await screen.findByText(/appear as speech arrives/i)).toBeInTheDocument();
    expect(screen.queryByText(/built from the finished transcript/)).not.toBeInTheDocument();
  });
});
