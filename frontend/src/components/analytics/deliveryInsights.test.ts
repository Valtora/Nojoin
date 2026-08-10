import { describe, expect, it } from "vitest";

import type { AnalyticsDelivery, AnalyticsDeliverySpeaker } from "@/types";

import { buildDeliveryReadings } from "./deliveryInsights";
import { formatLatency } from "./formatDuration";

const speaker = (
  overrides: Partial<AnalyticsDeliverySpeaker> = {},
): AnalyticsDeliverySpeaker => ({
  analysed_utterances: 40,
  words_per_minute: 140,
  median_f0_hz: 120,
  pitch_spread_semitones: 5,
  median_loudness_dbfs: -22,
  loudness_range_db: 6,
  capture_sources: [],
  pause_count: 10,
  median_pause_ms: 500,
  ...overrides,
});

const delivery = (
  speakers: Record<string, AnalyticsDeliverySpeaker>,
): AnalyticsDelivery => ({
  method_version: 1,
  speakers,
  cross_speaker_loudness_comparable: true,
  channel_layout: "single_source",
  skipped_overlapping: 0,
  skipped_short: 0,
  ambiguous_channel: 0,
});

// Ten minutes of speech each, so a pause count converts to a round rate.
const TEN_MINUTES = { "rs:1": 600_000, "rs:2": 600_000, "rs:3": 600_000 };

describe("buildDeliveryReadings", () => {
  it("describes pace against ordinary conversation", () => {
    const readings = buildDeliveryReadings(
      delivery({
        "rs:1": speaker({ words_per_minute: 95 }),
        "rs:2": speaker({ words_per_minute: 135 }),
        "rs:3": speaker({ words_per_minute: 205 }),
      }),
      TEN_MINUTES,
    );

    expect(readings["rs:1"]!.pace).toBe("deliberate");
    expect(readings["rs:2"]!.pace).toBe("conversational");
    expect(readings["rs:3"]!.pace).toBe("fast");
  });

  it("compares pitch movement only with the others in the meeting", () => {
    // The same expressive range measures about twice as wide on a high voice,
    // so an absolute band would be comparing vocal anatomy.
    const readings = buildDeliveryReadings(
      delivery({
        "rs:1": speaker({ pitch_spread_semitones: 9 }),
        "rs:2": speaker({ pitch_spread_semitones: 5 }),
        "rs:3": speaker({ pitch_spread_semitones: 2 }),
      }),
      TEN_MINUTES,
    );

    expect(readings["rs:1"]!.pitchMovement).toBe("more varied than others here");
    expect(readings["rs:2"]!.pitchMovement).toBeNull();
    expect(readings["rs:3"]!.pitchMovement).toBe("flatter than others here");
  });

  it("turns a pause count into a rate, so a long turn is not a hesitant one", () => {
    const readings = buildDeliveryReadings(
      delivery({
        "rs:1": speaker({ pause_count: 30 }),
        "rs:2": speaker({ pause_count: 10 }),
      }),
      { "rs:1": 600_000, "rs:2": 600_000 },
    );

    expect(readings["rs:1"]!.pauseRate).toBeCloseTo(3, 5);
    expect(readings["rs:2"]!.pauseRate).toBeCloseTo(1, 5);
    expect(readings["rs:1"]!.pauses).toBe("pauses more than others here");
  });

  it("says nothing about someone who barely spoke", () => {
    // A comparison drawn from three utterances describes the sample, not the
    // speaker. The absolute pace band still applies.
    const readings = buildDeliveryReadings(
      delivery({
        "rs:1": speaker({ analysed_utterances: 40 }),
        "rs:2": speaker({
          analysed_utterances: 2,
          pitch_spread_semitones: 12,
          words_per_minute: 200,
        }),
      }),
      TEN_MINUTES,
    );

    expect(readings["rs:2"]!.pitchMovement).toBeNull();
    expect(readings["rs:2"]!.pauses).toBeNull();
    expect(readings["rs:2"]!.pace).toBe("fast");
  });

  it("never describes pitch height, only how much it moved", () => {
    const readings = buildDeliveryReadings(
      delivery({ "rs:1": speaker({ median_f0_hz: 90 }) }),
      TEN_MINUTES,
    );

    // A descriptor for pitch height would characterise the person's voice
    // rather than their delivery, so the reading has no field for it.
    expect(Object.keys(readings["rs:1"]!).sort()).toEqual([
      "pace",
      "pauseRate",
      "pauses",
      "pitchMovement",
    ]);
  });

  it("copes with a speaker whose talk time is unknown", () => {
    const readings = buildDeliveryReadings(
      delivery({ "rs:1": speaker() }),
      {},
    );

    expect(readings["rs:1"]!.pauseRate).toBeNull();
    expect(readings["rs:1"]!.pauses).toBeNull();
  });
});

describe("formatLatency", () => {
  it("keeps sub-second reply gaps visible", () => {
    // Real medians sit here, and second-granular formatting rendered every one
    // of them as "0s".
    expect(formatLatency(280)).toBe("280ms");
    expect(formatLatency(350)).toBe("350ms");
    expect(formatLatency(600)).toBe("600ms");
  });

  it("switches to seconds once a gap is long enough to read as one", () => {
    expect(formatLatency(1_960)).toBe("2.0s");
    expect(formatLatency(4_250)).toBe("4.3s");
  });

  it("falls back to the coarse format for genuinely long gaps", () => {
    expect(formatLatency(75_000)).toBe("1m 15s");
  });
});
