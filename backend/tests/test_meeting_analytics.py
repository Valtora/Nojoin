"""Tests for the deterministic meeting-analytics tier.

The metric functions are pure over ``UtteranceRow``, so they are exercised
directly with hand-built fixtures rather than through a database. Each test
here pins a definition that the interface and the MCP surface both quote, so a
failure means a published number changed meaning, not that an internal detail
moved.
"""

from __future__ import annotations

from backend.services.meeting_analytics.constants import (
    LATENCY_FLOOR_MS,
    OVERLAP_FLOOR_MS,
    TIMELINE_MIN_BUCKET_MS,
    TURN_GAP_MS,
)
from backend.services.meeting_analytics.metrics import (
    UtteranceRow,
    build_turns,
    compute_deterministic_metrics,
    compute_interruptions,
    compute_overlap,
    compute_silence,
    compute_talk_time,
    compute_timeline,
    compute_transitions,
    merge_intervals,
)
from backend.services.meeting_analytics.warnings import build_attribution_warning


def row(speaker: str, start_ms: int, end_ms: int) -> UtteranceRow:
    return UtteranceRow(speaker_key=speaker, start_ms=start_ms, end_ms=end_ms)


class TestTalkTime:
    def test_shares_are_measured_against_speech_not_duration(self):
        # Two speakers, 10s each, inside a 60s recording. Share of speech is
        # half each; share of duration is a sixth each. Conflating the two is
        # the most common way a talk-share figure ends up wrong.
        utterances = [row("a", 0, 10_000), row("b", 20_000, 30_000)]

        result = compute_talk_time(utterances, duration_ms=60_000)

        assert result["a"]["share_of_speech"] == 0.5
        assert result["b"]["share_of_speech"] == 0.5
        assert result["a"]["share_of_duration"] == round(10_000 / 60_000, 4)

    def test_overlapping_speech_counts_for_both_speakers(self):
        # Both spoke for the full ten seconds. Each genuinely held the floor
        # for that time, so each is credited it, and the shares sum above the
        # wall clock by design.
        utterances = [row("a", 0, 10_000), row("b", 5_000, 15_000)]

        result = compute_talk_time(utterances, duration_ms=15_000)

        assert result["a"]["speech_ms"] == 10_000
        assert result["b"]["speech_ms"] == 10_000
        assert result["a"]["share_of_duration"] + result["b"]["share_of_duration"] > 1.0

    def test_reversed_timestamps_do_not_subtract_from_a_total(self):
        utterances = [row("a", 0, 5_000), row("a", 9_000, 8_000)]

        result = compute_talk_time(utterances, duration_ms=10_000)

        assert result["a"]["speech_ms"] == 5_000

    def test_empty_transcript(self):
        assert compute_talk_time([], duration_ms=60_000) == {}


class TestTurns:
    def test_gap_below_threshold_continues_the_turn(self):
        utterances = [row("a", 0, 1_000), row("a", 1_000 + TURN_GAP_MS - 1, 4_000)]

        turns = build_turns(utterances)

        assert len(turns) == 1
        assert turns[0].utterance_count == 2
        assert turns[0].end_ms == 4_000

    def test_gap_at_threshold_breaks_the_turn(self):
        utterances = [row("a", 0, 1_000), row("a", 1_000 + TURN_GAP_MS, 4_000)]

        turns = build_turns(utterances)

        assert len(turns) == 2

    def test_another_speaker_in_between_breaks_the_turn(self):
        # If someone else spoke, the first speaker did not hold the floor
        # continuously, however short the gap.
        utterances = [
            row("a", 0, 1_000),
            row("b", 1_100, 1_400),
            row("a", 1_500, 2_000),
        ]

        turns = build_turns(utterances)

        assert [turn.speaker_key for turn in turns] == ["a", "b", "a"]

    def test_longest_turn_carries_its_timestamp(self):
        utterances = [row("a", 0, 1_000), row("a", 30_000, 90_000)]

        metrics = compute_deterministic_metrics(utterances, duration_ms=120_000)
        structure = metrics["turn_structure"]["a"]

        assert structure["longest_turn_ms"] == 60_000
        assert structure["longest_turn_start_ms"] == 30_000

    def test_very_short_turns_are_excluded_from_the_median_and_counted(self):
        utterances = [row("a", 0, 100), row("a", 10_000, 20_000)]

        metrics = compute_deterministic_metrics(utterances, duration_ms=30_000)
        structure = metrics["turn_structure"]["a"]

        assert structure["turn_count"] == 2
        assert structure["median_turn_ms"] == 10_000
        assert structure["excluded_short_turns"] == 1


class TestInterruptions:
    def test_interruption_is_directional(self):
        # B starts while A still has 5s to run.
        utterances = [row("a", 0, 10_000), row("b", 5_000, 12_000)]

        counts = compute_interruptions(utterances)

        assert counts["b"]["made"] == 1
        assert counts["b"]["received"] == 0
        assert counts["a"]["received"] == 1
        assert counts["a"]["made"] == 0

    def test_turn_boundary_bleed_below_the_floor_is_not_an_interruption(self):
        utterances = [
            row("a", 0, 10_000),
            row("b", 10_000 - OVERLAP_FLOOR_MS + 50, 12_000),
        ]

        counts = compute_interruptions(utterances)

        assert counts["b"]["made"] == 0
        assert counts["a"]["received"] == 0

    def test_a_speaker_overlapping_themselves_is_not_an_interruption(self):
        utterances = [row("a", 0, 10_000), row("a", 3_000, 12_000)]

        counts = compute_interruptions(utterances)

        assert counts["a"]["made"] == 0
        assert counts["a"]["received"] == 0

    def test_one_utterance_interrupting_two_speakers_counts_once_for_the_interrupter(
        self,
    ):
        # A cuts across two people already talking. That is one interrupting
        # act, but two people were interrupted.
        utterances = [
            row("a", 0, 10_000),
            row("b", 0, 10_000),
            row("c", 5_000, 12_000),
        ]

        counts = compute_interruptions(utterances)

        assert counts["c"]["made"] == 1
        assert counts["a"]["received"] == 1
        assert counts["b"]["received"] == 1

    def test_simultaneous_starts_interrupt_nobody(self):
        # Neither speaker began first, so neither cut the other off. Without a
        # strict start comparison the sort order would pick a direction.
        utterances = [row("a", 0, 10_000), row("b", 0, 10_000)]

        counts = compute_interruptions(utterances)

        assert counts["a"] == {"made": 0, "received": 0}
        assert counts["b"] == {"made": 0, "received": 0}

    def test_being_interrupted_repeatedly_accumulates(self):
        utterances = [
            row("a", 0, 20_000),
            row("b", 5_000, 8_000),
            row("c", 12_000, 15_000),
        ]

        counts = compute_interruptions(utterances)

        assert counts["a"]["received"] == 2
        assert counts["b"]["made"] == 1
        assert counts["c"]["made"] == 1


class TestTransitionsAndLatency:
    def test_transition_matrix_counts_ordered_pairs(self):
        utterances = [
            row("a", 0, 5_000),
            row("b", 10_000, 15_000),
            row("a", 20_000, 25_000),
        ]

        result = compute_transitions(build_turns(utterances))
        pairs = {
            (item["from_speaker"], item["to_speaker"]): item["count"]
            for item in result["transitions"]
        }

        assert pairs == {("a", "b"): 1, ("b", "a"): 1}

    def test_samples_below_the_floor_are_excluded_and_counted(self):
        utterances = [
            row("a", 0, 5_000),
            row("b", 5_000 + LATENCY_FLOOR_MS - 10, 9_000),
        ]

        result = compute_transitions(build_turns(utterances))

        assert result["excluded_latency_samples"] == 1
        assert "b" not in result["response_latency"]
        # The transition itself is still recorded; only its timing is unusable.
        assert result["transitions"][0]["count"] == 1

    def test_negative_gaps_are_dropped_rather_than_clamped(self):
        # An overlapping handover is an interruption, already counted as one.
        # Clamping it to the floor would invent a fast responder.
        utterances = [row("a", 0, 10_000), row("b", 4_000, 14_000)]

        result = compute_transitions(build_turns(utterances))

        assert result["excluded_latency_samples"] == 1
        assert result["response_latency"] == {}

    def test_median_latency_is_reported_with_its_sample_count(self):
        utterances = [
            row("a", 0, 1_000),
            row("b", 3_000, 4_000),
            row("a", 6_000, 7_000),
            row("b", 11_000, 12_000),
        ]

        result = compute_transitions(build_turns(utterances))

        assert result["response_latency"]["b"]["sample_count"] == 2
        assert result["response_latency"]["b"]["median_ms"] == 3_000


class TestSilenceAndOverlap:
    def test_merge_intervals_collapses_overlaps(self):
        utterances = [
            row("a", 0, 10_000),
            row("b", 5_000, 12_000),
            row("a", 20_000, 25_000),
        ]

        assert merge_intervals(utterances) == [(0, 12_000), (20_000, 25_000)]

    def test_silence_is_duration_minus_non_overlapping_speech(self):
        utterances = [row("a", 0, 10_000), row("b", 5_000, 12_000)]

        result = compute_silence(utterances, duration_ms=20_000)

        assert result["speech_ms"] == 12_000
        assert result["silence_ms"] == 8_000
        assert result["silence_share"] == 0.4

    def test_overlap_share_is_double_counted_speech_over_total_speech(self):
        utterances = [row("a", 0, 10_000), row("b", 5_000, 10_000)]

        result = compute_overlap(utterances)

        assert result["overlapped_ms"] == 5_000
        assert result["overlap_share"] == round(5_000 / 15_000, 4)

    def test_silence_is_not_invented_when_duration_is_unknown(self):
        result = compute_silence([row("a", 0, 10_000)], duration_ms=0)

        assert result["silence_ms"] == 0
        assert result["silence_share"] == 0.0


class TestTimeline:
    def test_an_utterance_spanning_a_boundary_is_split_proportionally(self):
        # One 90s utterance across a 60s bucket boundary must not appear as a
        # spike in one bucket and silence in the next.
        utterances = [row("a", 30_000, 120_000)]

        result = compute_timeline(utterances, duration_ms=180_000)

        assert result["bucket_ms"] == TIMELINE_MIN_BUCKET_MS
        assert result["buckets"][0]["speech_ms"]["a"] == 30_000
        assert result["buckets"][1]["speech_ms"]["a"] == 60_000

    def test_bucket_width_never_drops_below_the_floor(self):
        result = compute_timeline([row("a", 0, 5_000)], duration_ms=10_000)

        assert result["bucket_ms"] == TIMELINE_MIN_BUCKET_MS

    def test_long_meetings_stay_within_the_target_bucket_count(self):
        four_hours_ms = 4 * 60 * 60 * 1000
        result = compute_timeline([row("a", 0, 1_000)], duration_ms=four_hours_ms)

        assert len(result["buckets"]) <= 41

    def test_empty_transcript_yields_one_empty_bucket(self):
        result = compute_timeline([], duration_ms=0)

        assert len(result["buckets"]) == 1
        assert result["buckets"][0]["speech_ms"] == {}


class TestAttributionWarning:
    def _speakers(self, count: int, named: bool = True) -> list[dict]:
        return [
            {"speaker_key": f"rs:{index}", "is_named": named} for index in range(count)
        ]

    def test_clean_recording_produces_no_warning(self):
        metrics = compute_deterministic_metrics(
            [row("rs:0", 0, 30_000), row("rs:1", 30_000, 60_000)],
            duration_ms=60_000,
        )

        warning = build_attribution_warning(
            metrics=metrics, speakers=self._speakers(2), max_speakers=None
        )

        assert warning is None

    def test_two_low_share_clusters_trigger_a_warning(self):
        metrics = compute_deterministic_metrics(
            [
                row("rs:0", 0, 100_000),
                row("rs:1", 100_000, 101_000),
                row("rs:2", 101_000, 102_000),
            ],
            duration_ms=120_000,
        )

        warning = build_attribution_warning(
            metrics=metrics, speakers=self._speakers(3), max_speakers=None
        )

        codes = {reason["code"] for reason in warning["reasons"]}
        assert "low_share_clusters" in codes

    def test_one_low_share_cluster_alone_does_not_trigger(self):
        metrics = compute_deterministic_metrics(
            [row("rs:0", 0, 100_000), row("rs:1", 100_000, 101_000)],
            duration_ms=120_000,
        )

        warning = build_attribution_warning(
            metrics=metrics, speakers=self._speakers(2), max_speakers=None
        )

        assert warning is None

    def test_a_bound_speaker_cap_is_disclosed(self):
        metrics = compute_deterministic_metrics(
            [row("rs:0", 0, 30_000), row("rs:1", 30_000, 60_000)],
            duration_ms=60_000,
        )

        warning = build_attribution_warning(
            metrics=metrics, speakers=self._speakers(2), max_speakers=2
        )

        codes = {reason["code"] for reason in warning["reasons"]}
        assert "speaker_cap_bound" in codes

    def test_unnamed_speakers_are_disclosed(self):
        metrics = compute_deterministic_metrics(
            [row("rs:0", 0, 30_000), row("rs:1", 30_000, 60_000)],
            duration_ms=60_000,
        )

        warning = build_attribution_warning(
            metrics=metrics, speakers=self._speakers(2, named=False), max_speakers=None
        )

        codes = {reason["code"] for reason in warning["reasons"]}
        assert "unnamed_speakers" in codes


class TestAssembly:
    def test_single_speaker_recording(self):
        metrics = compute_deterministic_metrics(
            [row("a", 0, 60_000)], duration_ms=60_000
        )

        assert metrics["talk_time"]["a"]["share_of_speech"] == 1.0
        assert metrics["turn_taking"]["transitions"] == []
        assert metrics["interruptions"]["a"] == {"made": 0, "received": 0}

    def test_empty_transcript_produces_a_complete_shape(self):
        metrics = compute_deterministic_metrics([], duration_ms=0)

        assert metrics["utterance_count"] == 0
        assert metrics["talk_time"] == {}
        assert metrics["turn_structure"] == {}
        assert metrics["timeline"]["buckets"]

    def test_duration_falls_back_to_the_last_utterance_end(self):
        metrics = compute_deterministic_metrics([row("a", 0, 45_000)], duration_ms=0)

        assert metrics["duration_ms"] == 45_000
