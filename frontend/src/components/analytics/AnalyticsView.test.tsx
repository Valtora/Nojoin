import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  renderWithProviders,
  screen,
  waitFor,
} from "@/test/renderWithProviders";
import type { RecordingAnalytics } from "@/types";

const getRecordingAnalytics = vi.fn();

vi.mock("@/lib/api", () => ({
  getRecordingAnalytics: (...args: unknown[]) => getRecordingAnalytics(...args),
}));

// Recharts measures its container, which jsdom reports as 0x0, so the SVG
// bodies never render here. That is fine and deliberate: the assertions below
// pin the figures and the disclosures, which are the parts that carry meaning
// and the parts a chart library cannot be trusted to get right on its own.
vi.mock("./TalkShareChart", () => ({
  default: () => <div data-testid="talk-share-chart" />,
}));
vi.mock("./TalkShareTimeline", () => ({
  default: () => <div data-testid="talk-share-timeline" />,
}));

import AnalyticsView from "./AnalyticsView";

const analytics = (
  overrides: Partial<RecordingAnalytics> = {},
): RecordingAnalytics => ({
  recording_id: "rec-1",
  transcript_revision: 7,
  speakers: [
    {
      speaker_key: "rs:1",
      public_id: "sp-1",
      name: "Dana",
      diarization_label: "SPEAKER_00",
      color: null,
      global_speaker_id: 11,
      is_named: true,
    },
    {
      speaker_key: "rs:2",
      public_id: "sp-2",
      name: "Guest",
      diarization_label: "SPEAKER_01",
      color: null,
      global_speaker_id: null,
      is_named: true,
    },
  ],
  metrics: {
    utterance_count: 4,
    duration_ms: 60_000,
    talk_time: {
      "rs:1": {
        speech_ms: 40_000,
        share_of_speech: 0.8,
        share_of_duration: 0.6667,
      },
      "rs:2": {
        speech_ms: 10_000,
        share_of_speech: 0.2,
        share_of_duration: 0.1667,
      },
    },
    turn_structure: {
      "rs:1": {
        turn_count: 2,
        median_turn_ms: 20_000,
        longest_turn_ms: 30_000,
        longest_turn_start_ms: 5_000,
        excluded_short_turns: 0,
      },
      "rs:2": {
        turn_count: 2,
        median_turn_ms: 5_000,
        longest_turn_ms: 5_000,
        longest_turn_start_ms: 25_000,
        excluded_short_turns: 0,
      },
    },
    interruptions: {
      "rs:1": { made: 0, received: 1 },
      "rs:2": { made: 1, received: 0 },
    },
    turn_taking: {
      transitions: [
        {
          from_speaker: "rs:1",
          to_speaker: "rs:2",
          count: 1,
          median_latency_ms: 5_000,
        },
      ],
      response_latency: { "rs:2": { median_ms: 5_000, sample_count: 1 } },
      excluded_latency_samples: 0,
    },
    timeline: {
      bucket_ms: 60_000,
      buckets: [{ start_ms: 0, end_ms: 60_000, speech_ms: { "rs:1": 40_000 } }],
    },
    silence: { speech_ms: 45_000, silence_ms: 15_000, silence_share: 0.25 },
    overlap: { overlapped_ms: 5_000, overlap_share: 0.1 },
  },
  attribution_warning: null,
  ...overrides,
});

describe("AnalyticsView", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getRecordingAnalytics.mockResolvedValue(analytics());
  });

  it("shows the headline figures and per-speaker breakdown", async () => {
    renderWithProviders(<AnalyticsView recordingId="rec-1" />);

    await waitFor(() => expect(screen.getByText("Dana")).toBeInTheDocument());
    // Silence and overlap are shares of different denominators; both appear.
    expect(screen.getByText("25%")).toBeInTheDocument();
    expect(screen.getByText("10%")).toBeInTheDocument();
    expect(screen.getByText("Guest")).toBeInTheDocument();
  });

  it("reports interruptions directionally rather than as one overlap count", async () => {
    renderWithProviders(<AnalyticsView recordingId="rec-1" />);

    await waitFor(() => expect(screen.getByText("Dana")).toBeInTheDocument());
    expect(screen.getByText("Interrupted")).toBeInTheDocument();
    expect(screen.getByText("Was interrupted")).toBeInTheDocument();
  });

  it("offers no attribution warning when nothing suggests a problem", async () => {
    renderWithProviders(<AnalyticsView recordingId="rec-1" />);

    await waitFor(() => expect(screen.getByText("Dana")).toBeInTheDocument());
    expect(
      screen.queryByText(/depend on who Nojoin thinks was speaking/),
    ).not.toBeInTheDocument();
  });

  it("discloses unreliable attribution when the backend flags it", async () => {
    getRecordingAnalytics.mockResolvedValue(
      analytics({
        attribution_warning: {
          reasons: [
            { code: "low_share_clusters", speaker_count: 3, speaker_keys: [] },
          ],
        },
      }),
    );

    renderWithProviders(<AnalyticsView recordingId="rec-1" />);

    await waitFor(() =>
      expect(
        screen.getByText(/depend on who Nojoin thinks was speaking/),
      ).toBeInTheDocument(),
    );
    expect(screen.getByText(/3 speakers hold under 3%/)).toBeInTheDocument();
  });

  it("names the unnamed speakers rather than referring to internal keys", async () => {
    getRecordingAnalytics.mockResolvedValue(
      analytics({
        attribution_warning: {
          reasons: [{ code: "unnamed_speakers", speaker_keys: ["rs:2"] }],
        },
      }),
    );

    renderWithProviders(<AnalyticsView recordingId="rec-1" />);

    await waitFor(() =>
      expect(screen.getByText(/Not everyone has been named/)).toBeInTheDocument(),
    );
    expect(screen.queryByText(/rs:2/)).not.toBeInTheDocument();
  });

  it("explains an unprocessed meeting instead of showing an empty chart", async () => {
    getRecordingAnalytics.mockResolvedValue(analytics({ speakers: [] }));

    renderWithProviders(<AnalyticsView recordingId="rec-1" />);

    await waitFor(() =>
      expect(screen.getByText("No analytics yet")).toBeInTheDocument(),
    );
    expect(screen.queryByTestId("talk-share-chart")).not.toBeInTheDocument();
  });

  it("offers a retry when analytics could not be loaded", async () => {
    getRecordingAnalytics.mockRejectedValue(new Error("boom"));

    renderWithProviders(<AnalyticsView recordingId="rec-1" />);

    await waitFor(() =>
      expect(screen.getByText("Try again")).toBeInTheDocument(),
    );
  });

  it("lets the longest turn be played, so a named monologue can be checked", async () => {
    const onPlaySegment = vi.fn();
    renderWithProviders(
      <AnalyticsView recordingId="rec-1" onPlaySegment={onPlaySegment} />,
    );

    await waitFor(() => expect(screen.getByText("Dana")).toBeInTheDocument());
    screen.getByTitle("Play from 0:05").click();

    expect(onPlaySegment).toHaveBeenCalledWith(5_000);
  });
});
