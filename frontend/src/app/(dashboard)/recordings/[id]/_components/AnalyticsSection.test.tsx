import { act } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders, screen, waitFor } from "@/test/renderWithProviders";
import type { LiveRecordingAnalytics } from "@/types";

const getRecordingAnalytics = vi.fn<(...args: unknown[]) => Promise<never>>(() => new Promise(() => {}));
const getLiveRecordingAnalytics = vi.fn();
vi.mock("@/lib/api", () => ({
  getRecordingAnalytics: (...args: unknown[]) => getRecordingAnalytics(...args),
  getLiveRecordingAnalytics: (...args: unknown[]) => getLiveRecordingAnalytics(...args),
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

describe("AnalyticsSection live lifecycle", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getLiveRecordingAnalytics.mockResolvedValue(live());
  });
  afterEach(() => vi.useRealTimers());

  it("requests estimates only while active and switches to final analytics on completion", async () => {
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
    expect(screen.queryByRole("button", { name: /Play|Generate|Analyse/i })).not.toBeInTheDocument();
    expect(screen.getAllByText("After processing").length).toBeGreaterThanOrEqual(4);
    view.rerender(<AnalyticsSection active recordingId="rec-1" speakerColors={{}} />);
    await waitFor(() => expect(getRecordingAnalytics).toHaveBeenCalledWith("rec-1"));
    expect(screen.queryByText("Alex")).not.toBeInTheDocument();
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

  it.each(["sequence", "names", "state"])("refreshes immediately on %s changes", async (kind) => {
    const view = renderWithProviders(<AnalyticsSection active inFlight recordingId="rec-1" speakerColors={{}}
      refreshKey="initial" />);
    await screen.findByText("Alex");
    getLiveRecordingAnalytics.mockResolvedValue(live("Dana"));
    view.rerender(<AnalyticsSection active inFlight recordingId="rec-1" speakerColors={{}}
      refreshKey={kind} />);
    expect(await screen.findByText("Dana")).toBeInTheDocument();
    expect(getLiveRecordingAnalytics).toHaveBeenCalledTimes(2);
  });

  it("ignores a late response after a newer name refresh and after completion", async () => {
    let resolveOld!: (value: LiveRecordingAnalytics) => void;
    getLiveRecordingAnalytics.mockImplementationOnce(() => new Promise((resolve) => { resolveOld = resolve; }));
    const view = renderWithProviders(<AnalyticsSection active inFlight recordingId="rec-1" speakerColors={{}}
      refreshKey="old" />);
    await waitFor(() => expect(getLiveRecordingAnalytics).toHaveBeenCalledTimes(1));
    getLiveRecordingAnalytics.mockResolvedValue(live("Dana"));
    view.rerender(<AnalyticsSection active inFlight recordingId="rec-1" speakerColors={{}} refreshKey="new" />);
    expect(await screen.findByText("Dana")).toBeInTheDocument();
    await act(async () => { resolveOld(live("Obsolete")); });
    expect(screen.queryByText("Obsolete")).not.toBeInTheDocument();
    let resolvePoll!: (value: LiveRecordingAnalytics) => void;
    getLiveRecordingAnalytics.mockImplementationOnce(() => new Promise((resolve) => { resolvePoll = resolve; }));
    view.rerender(<AnalyticsSection active inFlight recordingId="rec-1" speakerColors={{}} refreshKey="last" />);
    await waitFor(() => expect(getLiveRecordingAnalytics).toHaveBeenCalledTimes(3));
    view.rerender(<AnalyticsSection active recordingId="rec-1" speakerColors={{}} />);
    await waitFor(() => expect(getRecordingAnalytics).toHaveBeenCalledTimes(1));
    await act(async () => { resolvePoll(live("Late live")); });
    expect(screen.queryByText("Late live")).not.toBeInTheDocument();
  });

  it("recovers from a stalled request without overlapping reads", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    getLiveRecordingAnalytics.mockImplementationOnce(() => new Promise(() => {}));
    renderWithProviders(<AnalyticsSection active inFlight recordingId="rec-1" speakerColors={{}} />);
    await waitFor(() => expect(getLiveRecordingAnalytics).toHaveBeenCalledTimes(1));
    await act(async () => { await vi.advanceTimersByTimeAsync(12000); });
    expect(getLiveRecordingAnalytics).toHaveBeenCalledTimes(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(6000); });
    expect(await screen.findByText("Alex")).toBeInTheDocument();
  });

  it("keeps empty and undersampled results provisional without reporting a zero pace", async () => {
    const estimates = live();
    estimates.transcript_wpm["live:1"].words_per_minute = null;
    getLiveRecordingAnalytics.mockResolvedValue(estimates);
    const view = renderWithProviders(<AnalyticsSection active inFlight recordingId="rec-1" speakerColors={{}} />);
    expect(await screen.findByText("Not enough speech yet")).toBeInTheDocument();
    getLiveRecordingAnalytics.mockResolvedValue({ ...live(), speakers: [], metrics: {
      utterance_count: 0, talk_time: {}, turn_structure: {},
    }, transcript_wpm: {} });
    view.rerender(<AnalyticsSection active inFlight recordingId="rec-1" speakerColors={{}} refreshKey="empty" />);
    expect(await screen.findByText(/appear as speech arrives/i)).toBeInTheDocument();
    expect(screen.queryByText(/built from the finished transcript/)).not.toBeInTheDocument();
  });
});
