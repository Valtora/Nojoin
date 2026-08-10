import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  renderWithProviders,
  screen,
  waitFor,
} from "@/test/renderWithProviders";
import type { AnalyticsAi, RecordingAnalytics } from "@/types";

const getRecordingAnalytics = vi.fn();

const generateRecordingAnalytics = vi.fn();

const generateRecordingAiAnalytics = vi.fn();

vi.mock("@/lib/api", () => ({
  getRecordingAnalytics: (...args: unknown[]) => getRecordingAnalytics(...args),
  generateRecordingAnalytics: (...args: unknown[]) =>
    generateRecordingAnalytics(...args),
  generateRecordingAiAnalytics: (...args: unknown[]) =>
    generateRecordingAiAnalytics(...args),
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
      response_latency: {
        "rs:2": { median_ms: 1_200, sample_count: 3, immediate_count: 2 },
      },
      immediate_transitions: 2,
      lapse_transitions: 1,
    },
    timeline: {
      bucket_ms: 60_000,
      buckets: [{ start_ms: 0, end_ms: 60_000, speech_ms: { "rs:1": 40_000 } }],
    },
    silence: { speech_ms: 45_000, silence_ms: 15_000, silence_share: 0.25 },
    overlap: {
      overlapped_ms: 5_000,
      overlap_share: 0.1,
      overlapping_speech_present: true,
    },
  },
  attribution_warning: null,
  delivery: null,
  delivery_status: "pending",
  delivery_error_message: null,
  delivery_stale: false,
  ai: null,
  ai_status: "pending",
  ai_error_message: null,
  ai_stale: false,
  audio_overlap: null,
  audio_overlap_status: "pending",
  audio_overlap_error_message: null,
  delivery_baselines: {},
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
    expect(screen.getByText("25%")).toBeInTheDocument();
    // The talked-over tile shows a dash until overlap has been measured from
    // the audio: the transcript's own overlap figure is zero by construction.
    expect(screen.getByText("–")).toBeInTheDocument();
    expect(screen.getByText("Guest")).toBeInTheDocument();
  });

  it("shows floor-taking as instant handovers, never as interruption counts", async () => {
    // Per-speaker interruption counts are indefensible from a single audio
    // channel, so the surface reports how often each speaker took the floor
    // the instant it opened, which the timestamps do support.
    renderWithProviders(<AnalyticsView recordingId="rec-1" />);

    await waitFor(() => expect(screen.getByText("Dana")).toBeInTheDocument());
    expect(screen.getByText("Instant handovers")).toBeInTheDocument();
    expect(screen.queryByText("Interrupted")).not.toBeInTheDocument();
    expect(screen.queryByText("Was interrupted")).not.toBeInTheDocument();
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

  it("offers to measure delivery rather than showing it as absent", async () => {
    renderWithProviders(<AnalyticsView recordingId="rec-1" />);

    await waitFor(() => expect(screen.getByText("Dana")).toBeInTheDocument());
    expect(
      screen.getByRole("button", { name: "Measure delivery" }),
    ).toBeInTheDocument();
  });

  it("starts a delivery measurement on request", async () => {
    generateRecordingAnalytics.mockResolvedValue({
      recording_id: "rec-1",
      delivery_status: "generating",
    });

    renderWithProviders(<AnalyticsView recordingId="rec-1" />);
    await waitFor(() => expect(screen.getByText("Dana")).toBeInTheDocument());
    screen.getByRole("button", { name: "Measure delivery" }).click();

    await waitFor(() =>
      expect(generateRecordingAnalytics).toHaveBeenCalledWith("rec-1"),
    );
  });

  it("shows measured delivery as pace and pitch, never as mood", async () => {
    getRecordingAnalytics.mockResolvedValue(
      analytics({
        delivery_status: "completed",
        delivery: {
          method_version: 1,
          cross_speaker_loudness_comparable: true,
          channel_layout: "single_source",
          skipped_overlapping: 0,
          skipped_short: 0,
          ambiguous_channel: 0,
          speakers: {
            "rs:1": {
              analysed_utterances: 40,
              words_per_minute: 168,
              median_f0_hz: 112,
              pitch_spread_semitones: 3.4,
              median_loudness_dbfs: -22.5,
              loudness_range_db: 6.1,
              capture_sources: [],
              pause_count: 12,
              median_pause_ms: 620,
            },
          },
        },
      }),
    );

    renderWithProviders(<AnalyticsView recordingId="rec-1" />);

    await waitFor(() =>
      expect(screen.getByText("168 wpm")).toBeInTheDocument(),
    );
    expect(screen.getByText("3.4 st")).toBeInTheDocument();
    expect(
      screen.getByText(/describes how someone spoke, not how they felt/),
    ).toBeInTheDocument();
    // The raw figure keeps its plain-language reading beside it rather than
    // being replaced by one. 168 wpm sits in the corpus-calibrated
    // conversational band (160-200 by this measure).
    expect(screen.getByText("conversational")).toBeInTheDocument();
  });

  it("says why loudness is not compared when the sources differ", async () => {
    getRecordingAnalytics.mockResolvedValue(
      analytics({
        delivery_status: "completed",
        delivery: {
          method_version: 1,
          cross_speaker_loudness_comparable: false,
          channel_layout: "browser_live",
          skipped_overlapping: 0,
          skipped_short: 0,
          ambiguous_channel: 0,
          speakers: {
            "rs:1": {
              analysed_utterances: 40,
              words_per_minute: 168,
              median_f0_hz: 112,
              pitch_spread_semitones: 3.4,
              median_loudness_dbfs: -22.5,
              loudness_range_db: 6.1,
              capture_sources: ["microphone"],
              pause_count: 12,
              median_pause_ms: 620,
            },
          },
        },
      }),
    );

    renderWithProviders(<AnalyticsView recordingId="rec-1" />);

    await waitFor(() =>
      expect(
        screen.getByText(/different audio\s+sources/),
      ).toBeInTheDocument(),
    );
  });

  it("shows a sub-second reply time at the resolution the timing supports", async () => {
    getRecordingAnalytics.mockResolvedValue(
      analytics({
        metrics: {
          ...analytics().metrics,
          turn_taking: {
            transitions: [],
            response_latency: {
              "rs:1": { median_ms: 280, sample_count: 35, immediate_count: 12 },
            },
            immediate_transitions: 12,
            lapse_transitions: 0,
          },
        },
      }),
    );

    renderWithProviders(<AnalyticsView recordingId="rec-1" />);

    // Not "0s" (which hides it) and not "280ms" (which overclaims precision
    // the timestamps do not have).
    await waitFor(() => expect(screen.getByText("0.3s")).toBeInTheDocument());
    expect(screen.getByText("12")).toBeInTheDocument();
  });

  it("offers overlap measurement rather than showing it as absent", async () => {
    renderWithProviders(<AnalyticsView recordingId="rec-1" />);

    await waitFor(() =>
      expect(screen.getByText("Talking over each other")).toBeInTheDocument(),
    );
    expect(screen.getByText(/Not measured yet/)).toBeInTheDocument();
  });

  it("presents measured overlap as a floor, without naming who overlapped", async () => {
    getRecordingAnalytics.mockResolvedValue(
      analytics({
        audio_overlap_status: "completed",
        audio_overlap: {
          method_version: 1,
          total_overlap_ms: 84_000,
          overlap_share_of_audio: 0.042,
          region_count: 37,
          regions: [
            [10_000, 14_000],
            [30_000, 34_000],
          ],
          regions_truncated: false,
          duration_ms: 2_000_000,
        },
      }),
    );

    renderWithProviders(<AnalyticsView recordingId="rec-1" />);

    await waitFor(() =>
      expect(screen.getByText(/at least/)).toBeInTheDocument(),
    );
    expect(screen.getByText("1m 24s")).toBeInTheDocument();
    expect(screen.getByText("≥4.2%")).toBeInTheDocument();
    // The measured floor is disclosed, and nothing on the surface claims to
    // know who overlapped whom or calls it interruption.
    expect(
      screen.getByText(/does not guess who did it to whom/),
    ).toBeInTheDocument();
    expect(screen.queryByText(/interruption count/i)).not.toBeInTheDocument();
  });

  it("reports a stale measurement rather than quietly serving old figures", async () => {
    getRecordingAnalytics.mockResolvedValue(
      analytics({
        delivery_status: "completed",
        delivery_stale: true,
        delivery: {
          method_version: 1,
          cross_speaker_loudness_comparable: true,
          channel_layout: "single_source",
          skipped_overlapping: 0,
          skipped_short: 0,
          ambiguous_channel: 0,
          speakers: {
            "rs:1": {
              analysed_utterances: 40,
              words_per_minute: 168,
              median_f0_hz: 112,
              pitch_spread_semitones: 3.4,
              median_loudness_dbfs: -22.5,
              loudness_range_db: 6.1,
              capture_sources: [],
              pause_count: 12,
              median_pause_ms: 620,
            },
          },
        },
      }),
    );

    renderWithProviders(<AnalyticsView recordingId="rec-1" />);

    await waitFor(() =>
      expect(
        screen.getByText(/transcript has changed since this was measured/),
      ).toBeInTheDocument(),
    );
  });
});

const aiPayload = (overrides: Partial<AnalyticsAi> = {}): AnalyticsAi => ({
  method_version: 1,
  computed_at: "2026-08-10T09:00:00Z",
  event_watermark: 7,
  topics: [
    {
      title: "Rollout plan",
      start_ms: 0,
      end_ms: 30_000,
      summary: "How widely to ship the pilot.",
      led_by: "rs:1",
      contested: false,
      leadership_basis: "Proposed the pilot.",
    },
    {
      title: "Timeline",
      start_ms: 30_000,
      end_ms: 60_000,
      summary: "Whether March is reachable.",
      led_by: null,
      contested: true,
      leadership_basis: null,
    },
  ],
  sentiment: [
    {
      speaker_key: "rs:2",
      tone: "mixed",
      summary: "Backed the approach, doubted the date.",
      citations: [
        { quote: "Not by March.", start_ms: 12_000, speaker_key: "rs:2" },
      ],
    },
  ],
  questions: [
    {
      question: "Who owns the migration?",
      asked_by: "rs:1",
      asked_at_ms: 20_000,
      answered_by: null,
      answered_at_ms: null,
      answer_summary: null,
    },
  ],
  decisions: [
    {
      decision: "Pilot with two customers first.",
      proposed_by: "rs:1",
      agreed_by: [],
      objected_by: ["rs:2"],
      consensus: "assumed",
      citations: [
        { quote: "Two customers first.", start_ms: 4_000, speaker_key: "rs:1" },
      ],
    },
  ],
  excluded: {
    unknown_speaker_items: 0,
    uncited_sentiment: 0,
    uncited_decisions: 0,
    unverifiable_citations: 0,
    out_of_range_citations: 0,
    malformed_items: 0,
    ambiguous_speaker_names: 0,
  },
  transcript_truncated: false,
  analysed_through_ms: 60_000,
  ...overrides,
});

describe("AnalyticsView AI tier", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getRecordingAnalytics.mockResolvedValue(analytics());
  });

  it("offers the analysis rather than showing it as missing", async () => {
    renderWithProviders(<AnalyticsView recordingId="rec-1" />);

    await waitFor(() => expect(screen.getByText("Dana")).toBeInTheDocument());
    expect(
      screen.getByRole("button", { name: "Analyse meeting" }),
    ).toBeInTheDocument();
  });

  it("starts the analysis on request", async () => {
    generateRecordingAiAnalytics.mockResolvedValue({
      recording_id: "rec-1",
      ai_status: "generating",
    });

    renderWithProviders(<AnalyticsView recordingId="rec-1" />);
    await waitFor(() => expect(screen.getByText("Dana")).toBeInTheDocument());
    screen.getByRole("button", { name: "Analyse meeting" }).click();

    await waitFor(() =>
      expect(generateRecordingAiAnalytics).toHaveBeenCalledWith("rec-1"),
    );
  });

  it("stays visibly busy while the worker has not yet claimed the task", async () => {
    // The POST returns as soon as the task is queued, so the read that follows
    // it still reports "pending". Clearing the busy state on that read dropped
    // the user back to the button with nothing happening, and invited a second
    // click that would spend the AI quota twice.
    generateRecordingAiAnalytics.mockResolvedValue({
      recording_id: "rec-1",
      ai_status: "generating",
    });
    getRecordingAnalytics.mockResolvedValue(analytics({ ai_status: "pending" }));

    renderWithProviders(<AnalyticsView recordingId="rec-1" />);
    await waitFor(() => expect(screen.getByText("Dana")).toBeInTheDocument());
    screen.getByRole("button", { name: "Analyse meeting" }).click();

    await waitFor(() =>
      expect(screen.getByText(/Analysing the meeting/)).toBeInTheDocument(),
    );
    expect(
      screen.queryByRole("button", { name: "Analyse meeting" }),
    ).not.toBeInTheDocument();
  });

  it("treats a missing AI provider as normal rather than as a failure", async () => {
    getRecordingAnalytics.mockResolvedValue(
      analytics({ ai_status: "unavailable" }),
    );

    renderWithProviders(<AnalyticsView recordingId="rec-1" />);

    await waitFor(() =>
      expect(
        screen.getByText(/needs an AI provider, and none is configured/),
      ).toBeInTheDocument(),
    );
    // No retry button: there is nothing here for the user to retry.
    expect(
      screen.queryByRole("button", { name: "Analyse meeting" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Try again" }),
    ).not.toBeInTheDocument();
    // The measured tiers are unaffected by the AI tier being unavailable.
    expect(screen.getByText("Dana")).toBeInTheDocument();
  });

  it("reports a failed analysis with a way to retry", async () => {
    getRecordingAnalytics.mockResolvedValue(
      analytics({
        ai_status: "error",
        ai_error_message: "This meeting could not be analysed.",
      }),
    );

    renderWithProviders(<AnalyticsView recordingId="rec-1" />);

    await waitFor(() =>
      expect(
        screen.getByText("This meeting could not be analysed."),
      ).toBeInTheDocument(),
    );
    expect(
      screen.getByRole("button", { name: "Try again" }),
    ).toBeInTheDocument();
  });

  it("says the analysis is running while it is", async () => {
    getRecordingAnalytics.mockResolvedValue(
      analytics({ ai_status: "generating" }),
    );

    renderWithProviders(<AnalyticsView recordingId="rec-1" />);

    await waitFor(() =>
      expect(screen.getByText(/Analysing the meeting/)).toBeInTheDocument(),
    );
  });

  it("shows topics, sentiment, questions and decision ownership", async () => {
    getRecordingAnalytics.mockResolvedValue(
      analytics({ ai_status: "completed", ai: aiPayload() }),
    );

    renderWithProviders(<AnalyticsView recordingId="rec-1" />);

    await waitFor(() =>
      expect(screen.getByText("Rollout plan")).toBeInTheDocument(),
    );
    expect(screen.getByText("Led by Dana")).toBeInTheDocument();
    // A topic nobody owned says so rather than picking somebody.
    expect(
      screen.getByText("Led jointly, or by no one in particular"),
    ).toBeInTheDocument();
    expect(screen.getByText("Mixed")).toBeInTheDocument();
    expect(screen.getByText("Unanswered")).toBeInTheDocument();
    expect(screen.getByText("Pilot with two customers first.")).toBeInTheDocument();
    // "Assumed" consensus must never read as agreement. It appears twice: as
    // the badge on the decision, and in the note explaining what it means.
    expect(screen.getAllByText("Unchallenged")).toHaveLength(2);
    expect(
      screen.getByText(/means nobody objected, which is not the same as agreement/),
    ).toBeInTheDocument();
    expect(screen.getByText("Pushed back")).toBeInTheDocument();
  });

  it("shows the quote behind every claim about a person", async () => {
    getRecordingAnalytics.mockResolvedValue(
      analytics({ ai_status: "completed", ai: aiPayload() }),
    );

    renderWithProviders(<AnalyticsView recordingId="rec-1" />);

    await waitFor(() =>
      expect(screen.getByText(/Not by March\./)).toBeInTheDocument(),
    );
    expect(screen.getByText(/Two customers first\./)).toBeInTheDocument();
  });

  it("plays back the quote behind a claim so it can be checked", async () => {
    const onPlaySegment = vi.fn();
    getRecordingAnalytics.mockResolvedValue(
      analytics({ ai_status: "completed", ai: aiPayload() }),
    );

    renderWithProviders(
      <AnalyticsView recordingId="rec-1" onPlaySegment={onPlaySegment} />,
    );

    await waitFor(() =>
      expect(screen.getByText(/Not by March\./)).toBeInTheDocument(),
    );
    screen.getByTitle("Play from 0:12").click();

    expect(onPlaySegment).toHaveBeenCalledWith(12_000);
  });

  it("keeps sentiment and measured delivery visibly separate", async () => {
    getRecordingAnalytics.mockResolvedValue(
      analytics({ ai_status: "completed", ai: aiPayload() }),
    );

    renderWithProviders(<AnalyticsView recordingId="rec-1" />);

    await waitFor(() =>
      expect(
        screen.getByText(/not combined with the measured delivery figures/),
      ).toBeInTheDocument(),
    );
  });

  it("reports a stale analysis rather than quietly serving an old one", async () => {
    getRecordingAnalytics.mockResolvedValue(
      analytics({ ai_status: "completed", ai_stale: true, ai: aiPayload() }),
    );

    renderWithProviders(<AnalyticsView recordingId="rec-1" />);

    await waitFor(() =>
      expect(
        screen.getByText(/transcript has changed since this was written/),
      ).toBeInTheDocument(),
    );
  });

  it("discloses a meeting that was too long to analyse in full", async () => {
    getRecordingAnalytics.mockResolvedValue(
      analytics({
        ai_status: "completed",
        ai: aiPayload({ transcript_truncated: true, analysed_through_ms: 45_000 }),
      }),
    );

    renderWithProviders(<AnalyticsView recordingId="rec-1" />);

    await waitFor(() =>
      expect(
        screen.getByText(/too long to analyse in full/),
      ).toBeInTheDocument(),
    );
    expect(screen.getByText(/0:45 only/)).toBeInTheDocument();
  });
});
