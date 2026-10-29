import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AnalyticsAi } from "@/types";
import type { RecordingAiAnalytics } from "@/lib/api";

const api = vi.hoisted(() => ({ getRecordingAiAnalytics: vi.fn(), generateRecordingAiAnalytics: vi.fn() }));
vi.mock("@/lib/api", () => api);

import LiveDiscussionAnalysis from "./LiveDiscussionAnalysis";

const ai = (): AnalyticsAi => ({
  method_version: 1, computed_at: "2026-10-29T10:00:00Z", event_watermark: 4,
  topics: [{ title: "Earlier discussion", summary: "An earlier snapshot", start_ms: 0, end_ms: 12000,
    led_by: null, contested: false, leadership_basis: null }],
  sentiment: [], questions: [], decisions: [],
  excluded: { unknown_speaker_items: 0, uncited_sentiment: 0, uncited_decisions: 0,
    unverifiable_citations: 0, out_of_range_citations: 0, malformed_items: 0, ambiguous_speaker_names: 0 },
  transcript_truncated: false, analysed_through_ms: 12000,
});
const analysis = (overrides: Partial<RecordingAiAnalytics> = {}): RecordingAiAnalytics => ({
  recording_id: "rec-1", transcript_revision: 4, ai: null, ai_status: "pending", ai_error_message: null,
  ai_generation: {},
  ai_stale: false, ai_budget: { limit: null, remaining: null, used: 0, resets_at: "2026-10-30T00:00:00Z" },
  ...overrides,
});
const panel = () => <LiveDiscussionAnalysis recordingId="rec-1" speakers={[]} speakerColors={{}} />;

describe("independent live discussion", () => {
  beforeEach(() => {
    api.getRecordingAiAnalytics.mockReset().mockResolvedValue(analysis());
    api.generateRecordingAiAnalytics.mockReset().mockResolvedValue({ ai_status: "generating" });
  });
  afterEach(() => vi.useRealTimers());

  it("starts a live snapshot and polls it through completion", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    render(panel());
    fireEvent.click(await screen.findByRole("button", { name: "Analyse meeting" }));
    api.getRecordingAiAnalytics.mockResolvedValue(analysis({ ai_status: "generating" }));
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Analysing the meeting"));
    api.getRecordingAiAnalytics.mockResolvedValue(analysis({ ai_status: "completed", ai: ai(),
      ai_generation: { generated_through_ms: 12000, request_id: "run-1" } }));
    await act(() => vi.advanceTimersByTimeAsync(3000));
    expect(screen.getByText("Earlier discussion")).toBeInTheDocument();
    expect(screen.getByText(/transcript through 0:12/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Analyse again" })).toBeEnabled();
  });

  it("retains the preceding result during regeneration and after an independent failure", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    api.getRecordingAiAnalytics.mockResolvedValue(analysis({ ai: ai(), ai_status: "completed" }));
    render(panel());
    fireEvent.click(await screen.findByRole("button", { name: "Analyse again" }));
    api.getRecordingAiAnalytics.mockResolvedValue(analysis({ ai: ai(), ai_status: "generating" }));
    await screen.findByText(/Analysing the latest transcript snapshot/);
    expect(screen.getByText("Earlier discussion")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Analyse again" })).not.toBeInTheDocument();
    api.getRecordingAiAnalytics.mockResolvedValue(analysis({ ai: ai(), ai_status: "error",
      ai_error_message: "The provider timed out" }));
    await act(() => vi.advanceTimersByTimeAsync(3000));
    expect(screen.getByText("The provider timed out")).toBeInTheDocument();
    expect(screen.getByText("Earlier discussion")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try again" })).toBeEnabled();
  });

  it("keeps prior discussion visible when its start request or refresh fails", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    api.getRecordingAiAnalytics.mockResolvedValue(analysis({ ai: ai(), ai_status: "completed" }));
    api.generateRecordingAiAnalytics.mockRejectedValue({ response: { data: { detail: "Daily budget used up" } } });
    render(panel());
    fireEvent.click(await screen.findByRole("button", { name: "Analyse again" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Daily budget used up");
    expect(screen.getByText("Earlier discussion")).toBeInTheDocument();
    api.getRecordingAiAnalytics.mockRejectedValue(new Error("Offline"));
    await act(() => vi.advanceTimersByTimeAsync(3000));
    expect(screen.getByRole("status")).toHaveTextContent("could not be refreshed");
    expect(screen.getByText("Earlier discussion")).toBeInTheDocument();
  });

  it("ignores a read started before a generation request and aborts polling on unmount", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    api.getRecordingAiAnalytics.mockResolvedValue(analysis({ ai: ai(), ai_status: "completed" }));
    const view = render(panel());
    await screen.findByText("Earlier discussion");
    let resolveOld!: (value: RecordingAiAnalytics) => void;
    api.getRecordingAiAnalytics.mockImplementationOnce(() => new Promise((resolve) => { resolveOld = resolve; }));
    await act(() => vi.advanceTimersByTimeAsync(3000));
    api.getRecordingAiAnalytics.mockResolvedValue(analysis({ ai: ai(), ai_status: "generating" }));
    fireEvent.click(screen.getByRole("button", { name: "Analyse again" }));
    await screen.findByText(/Analysing the latest transcript snapshot/);
    await act(async () => resolveOld(analysis({ ai: ai(), ai_status: "completed" })));
    expect(screen.getByText(/Analysing the latest transcript snapshot/)).toBeInTheDocument();
    const lastSignal = api.getRecordingAiAnalytics.mock.lastCall?.[1] as AbortSignal;
    view.unmount();
    expect(lastSignal.aborted).toBe(true);
    const reads = api.getRecordingAiAnalytics.mock.calls.length;
    await act(() => vi.advanceTimersByTimeAsync(6000));
    expect(api.getRecordingAiAnalytics).toHaveBeenCalledTimes(reads);
  });
});
