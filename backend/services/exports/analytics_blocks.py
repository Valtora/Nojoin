"""The Analytics tab as export blocks: the measured sections.

Laid out in the order and with the wording the tab uses, so a reader of the
file sees what a reader of the app sees. Sources for the wording are the
components under ``frontend/src/components/analytics/``. The parts the tab
hides behind a "How this is measured" link are printed in small type under
their section, because a reader of a file cannot click through to them.

Nothing here starts a measurement. A tier that has not been measured is
reported as such, with where to measure it, and never blocks the export.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from . import theme
from .analytics_ai_blocks import ai_blocks
from .charts import (
    DensityChart,
    ShareBar,
    TalkShareChart,
    TimelineChart,
    TimelineSeries,
)
from .delivery_readings import DeliveryReading, build_delivery_readings
from .formatting import (
    format_duration,
    format_latency,
    format_share,
    format_timestamp,
    plural,
)
from .model import (
    LINE_BREAK,
    Block,
    Callout,
    CalloutTone,
    ChartBlock,
    Heading,
    Inline,
    ListBlock,
    ListItem,
    Paragraph,
    Span,
    Stat,
    StatRow,
    Table,
    plain,
)

DENSITY_BINS = 48
_BAR_CELLS = 20
_GENERATING = (
    "This was being measured when the file was exported, so it is not included."
)


def _note(*paragraphs: str) -> tuple[Block, ...]:
    return tuple(Paragraph(plain(text), tone="small") for text in paragraphs)


def _hint(text: str) -> Paragraph:
    return Paragraph(plain(text), tone="muted")


def _notice(text: str, tone: CalloutTone = "neutral") -> Callout:
    return Callout(tone=tone, title=None, body=(Paragraph(plain(text)),))


# -- attribution and headline figures ---------------------------------------


def _names(keys: Sequence[str] | None, speakers: Sequence[Mapping[str, Any]]) -> str:
    by_key = {speaker["speaker_key"]: speaker["name"] for speaker in speakers}
    return ", ".join(by_key.get(key, key) for key in keys or [])


def _describe_reason(
    reason: Mapping[str, Any], speakers: Sequence[Mapping[str, Any]]
) -> str:
    """The wording from AttributionWarning.tsx, which owns these reason codes."""
    code = reason.get("code")
    if code == "low_share_clusters":
        return (
            f"{reason.get('speaker_count')} speakers hold under 3% of the talking each. "
            "If one person was split into several, these shares are wrong."
        )
    if code == "high_overlap":
        share = round((reason.get("overlap_share") or 0) * 100)
        return (
            f"People talked over each other for {share}% of the speech. "
            "Attribution is least reliable where voices overlap."
        )
    if code == "speaker_cap_bound":
        return (
            f"This meeting reached its speaker limit of {reason.get('max_speakers')}. "
            "Speakers beyond the limit were merged into the ones shown."
        )
    if code == "unnamed_speakers":
        return f"Not everyone has been named yet: {_names(reason.get('speaker_keys'), speakers)}."
    return ""


def attribution_blocks(analytics: Mapping[str, Any]) -> tuple[Block, ...]:
    warning = analytics.get("attribution_warning") or {}
    messages = [
        _describe_reason(reason, analytics["speakers"])
        for reason in warning.get("reasons") or []
    ]
    messages = [message for message in messages if message]
    if not messages:
        return ()
    return (
        Callout(
            tone="warning",
            title="These figures depend on who Nojoin thinks was speaking",
            body=(ListBlock(tuple(ListItem(plain(message)) for message in messages)),),
        ),
    )


def headline_blocks(analytics: Mapping[str, Any]) -> tuple[Block, ...]:
    metrics = analytics["metrics"]
    overlap = analytics.get("audio_overlap")
    talked_over = (
        f"≥{format_share(overlap['overlap_share_of_audio'])}" if overlap else "–"
    )
    return (
        StatRow(
            (
                Stat("Speaking time", format_duration(metrics["silence"]["speech_ms"])),
                Stat("Speakers", str(len(analytics["speakers"]))),
                Stat("Silence", format_share(metrics["silence"]["silence_share"])),
                Stat("Talked over", talked_over),
            )
        ),
    )


# -- who spoke, and when ------------------------------------------------------


def _text_bar(share: float) -> str:
    filled = round(share * _BAR_CELLS)
    return "█" * filled + "░" * (_BAR_CELLS - filled)


def who_spoke_blocks(
    analytics: Mapping[str, Any], colors: Mapping[str, str]
) -> tuple[Block, ...]:
    talk_time = analytics["metrics"]["talk_time"]
    bars = []
    rows = []
    for speaker in analytics["speakers"]:
        figures = talk_time.get(speaker["speaker_key"], {})
        share = float(figures.get("share_of_speech") or 0.0)
        speech = format_duration(figures.get("speech_ms") or 0)
        bars.append(
            ShareBar(
                speaker["name"],
                share,
                format_share(share),
                speech,
                colors[speaker["speaker_key"]],
            )
        )
        rows.append(
            (
                plain(speaker["name"]),
                plain(format_share(share)),
                plain(speech),
                plain(_text_bar(share)),
            )
        )
    return (
        Heading(1, plain("Who spoke")),
        _hint(
            "Share of everyone's speaking time. Overlapping speech counts for each person, "
            "so this is a share of speech rather than of the meeting's length."
        ),
        ChartBlock(
            TalkShareChart(tuple(bars)),
            fallback=(
                Table(
                    header=(
                        plain("Speaker"),
                        plain("Share"),
                        plain("Speaking time"),
                        (),
                    ),
                    rows=tuple(rows),
                    numeric=frozenset({1, 2}),
                ),
            ),
        ),
    )


def _timeline_table(
    analytics: Mapping[str, Any], buckets: Sequence[Mapping[str, Any]]
) -> Table:
    speakers = analytics["speakers"]
    rows = tuple(
        (plain(format_timestamp(bucket["start_ms"])),)
        + tuple(
            plain(
                format_duration(value)
                if (value := bucket["speech_ms"].get(s["speaker_key"], 0))
                else "–"
            )
            for s in speakers
        )
        for bucket in buckets
    )
    return Table(
        header=(plain("From"),) + tuple(plain(s["name"]) for s in speakers),
        rows=rows,
        numeric=frozenset(range(1, len(speakers) + 1)),
    )


def who_spoke_when_blocks(
    analytics: Mapping[str, Any], colors: Mapping[str, str]
) -> tuple[Block, ...]:
    buckets = analytics["metrics"]["timeline"]["buckets"]
    if len(buckets) < 2:
        return ()
    series = tuple(
        TimelineSeries(
            speaker["name"],
            colors[speaker["speaker_key"]],
            tuple(
                int(bucket["speech_ms"].get(speaker["speaker_key"], 0))
                for bucket in buckets
            ),
        )
        for speaker in analytics["speakers"]
    )
    return (
        Heading(1, plain("Who spoke when")),
        _hint("Speaking time per speaker across the meeting."),
        ChartBlock(
            TimelineChart(tuple(int(bucket["start_ms"]) for bucket in buckets), series),
            fallback=(_timeline_table(analytics, buckets),),
        ),
    )


# -- how the conversation moved ---------------------------------------------


def _turn_row(
    speaker: Mapping[str, Any], metrics: Mapping[str, Any]
) -> tuple[Inline, ...]:
    key = speaker["speaker_key"]
    structure = metrics["turn_structure"].get(key) or {}
    latency = metrics["turn_taking"]["response_latency"].get(key) or {}
    longest: Inline = plain(format_duration(structure.get("longest_turn_ms") or 0))
    if structure.get("longest_turn_ms"):
        longest += (
            LINE_BREAK,
            Span(
                f"at {format_timestamp(structure.get('longest_turn_start_ms') or 0)}",
                color=theme.HELPER,
            ),
        )
    median_reply = latency.get("median_ms")
    return (
        plain(speaker["name"], bold=True),
        plain(str(structure.get("turn_count") or 0)),
        plain(format_duration(structure.get("median_turn_ms") or 0)),
        longest,
        plain(str(latency.get("immediate_count") or 0)),
        plain(format_latency(median_reply) if median_reply is not None else "-"),
    )


def turns_blocks(
    analytics: Mapping[str, Any], colors: Mapping[str, str]
) -> tuple[Block, ...]:
    speakers = analytics["speakers"]
    metrics = analytics["metrics"]
    return (
        Heading(1, plain("How the conversation moved")),
        _hint(
            "A long median turn means holding the floor; a short one means dialogue."
        ),
        Table(
            header=tuple(
                plain(label)
                for label in (
                    "Speaker",
                    "Turns",
                    "Median turn",
                    "Longest turn",
                    "Instant handovers",
                    "Replies after",
                )
            ),
            rows=tuple(_turn_row(speaker, metrics) for speaker in speakers),
            numeric=frozenset({1, 2, 3, 4, 5}),
            row_colors=tuple(colors[speaker["speaker_key"]] for speaker in speakers),
        ),
        *_note(
            "An instant handover is taking the floor within a quarter of a second of the "
            "previous speaker stopping, quicker than the timing can measure, and usually a "
            "reply prepared while the other person was still talking.",
            "Reply time is the median over gaps long enough to measure. A turn taken after "
            "more than five seconds of silence is a fresh start rather than a reply, so it "
            "is left out. Treat differences under a quarter of a second as noise.",
        ),
    )


# -- talking over each other ------------------------------------------------


def density_bins(overlap: Mapping[str, Any]) -> tuple[float, ...]:
    """Overlap time per bin, scaled to the busiest bin. Ports OverlapPanel.tsx."""
    bins = [0.0] * DENSITY_BINS
    span = max(float(overlap.get("duration_ms") or 0), 1.0)
    bin_ms = span / DENSITY_BINS
    for start, end in overlap.get("regions") or []:
        first = min(int(start // bin_ms), DENSITY_BINS - 1)
        last = min(int(max(end - 1, 0) // bin_ms), DENSITY_BINS - 1)
        for index in range(first, last + 1):
            bin_start = index * bin_ms
            bins[index] += max(
                0.0, min(end, bin_start + bin_ms) - max(start, bin_start)
            )
    peak = max(max(bins), 1.0)
    return tuple(value / peak for value in bins)


def _overlap_status_blocks(analytics: Mapping[str, Any]) -> tuple[Block, ...] | None:
    status = analytics.get("audio_overlap_status") or "pending"
    if status == "generating":
        return (_notice(_GENERATING),)
    if status == "error":
        return (
            _notice(
                analytics.get("audio_overlap_error_message")
                or "Overlapping speech could not be measured for this recording."
            ),
        )
    if not analytics.get("audio_overlap") or status != "completed":
        return (
            _notice(
                "Not measured yet. Measure delivery on the Analytics tab also listens "
                "for overlapping speech."
            ),
        )
    return None


def overlap_blocks(analytics: Mapping[str, Any]) -> tuple[Block, ...]:
    heading: tuple[Block, ...] = (
        Heading(1, plain("Talking over each other")),
        _hint(
            "Detected from the audio, because the transcript writes speech down one line at a time."
        ),
    )
    absent = _overlap_status_blocks(analytics)
    if absent is not None:
        return heading + absent
    overlap = analytics["audio_overlap"]
    count = int(overlap.get("region_count") or 0)
    sentence = Paragraph(
        (
            Span("People spoke over each other for at least "),
            Span(format_duration(overlap.get("total_overlap_ms") or 0), bold=True),
            Span(
                f" of this meeting ({format_share(overlap.get('overlap_share_of_audio') or 0)}), "
                f"across {plural(count, 'moment')}."
            ),
        )
    )
    blocks: list[Block] = [*heading, sentence]
    if count:
        bins = density_bins(overlap)
        blocks.append(
            ChartBlock(
                DensityChart(bins),
                fallback=(
                    Paragraph(
                        plain("Where it clustered: " + _density_text(bins)),
                        tone="muted",
                    ),
                ),
            )
        )
    blocks.extend(
        _note(
            "Detected from the audio itself, so it works even though the transcript writes "
            "speech down one line at a time. The figure is a floor: detection misses some "
            "overlap and never invents any.",
            "Talking over each other is not the same as interrupting. It includes agreement, "
            "encouragement and finishing each other's sentences, so Nojoin reports where it "
            "happened and does not guess who did it to whom.",
        )
    )
    return tuple(blocks)


def _density_text(bins: Sequence[float]) -> str:
    steps = " ▁▂▃▄▅▆▇█"
    return "start " + "".join(steps[round(value * 8)] for value in bins) + " end"


# -- how people spoke ---------------------------------------------------------


def _delivery_status_blocks(analytics: Mapping[str, Any]) -> tuple[Block, ...] | None:
    status = analytics.get("delivery_status") or "pending"
    if status == "generating":
        return (_notice(_GENERATING),)
    if status == "error":
        return (
            _notice(
                analytics.get("delivery_error_message")
                or "Delivery could not be measured."
            ),
        )
    if not analytics.get("delivery") or status != "completed":
        return (
            _notice(
                "Not measured yet. Delivery is measured from the meeting's audio on request: "
                "use Measure delivery on the Analytics tab."
            ),
        )
    return None


def _figure(value: str, reading: str | None) -> Inline:
    if not reading:
        return plain(value)
    return (Span(value), LINE_BREAK, Span(reading, color=theme.HELPER))


def _delivery_row(
    speaker: Mapping[str, Any], figures: Mapping[str, Any], reading: DeliveryReading
) -> tuple[Inline, ...]:
    name: Inline = plain(speaker["name"], bold=True)
    if reading.baseline:
        name += (LINE_BREAK, Span(reading.baseline, color=theme.HELPER))
    wpm = figures.get("words_per_minute")
    pitch = figures.get("median_f0_hz")
    spread = figures.get("pitch_spread_semitones")
    pauses = str(figures.get("pause_count") or 0)
    if reading.pause_rate is not None:
        pauses = f"{pauses} ({reading.pause_rate:.1f}/min)"
    return (
        name,
        _figure(f"{wpm} wpm" if wpm else "-", reading.pace),
        # No reading: a descriptor here would characterise someone's voice.
        plain(f"{pitch} Hz" if pitch else "-"),
        _figure(f"{spread} st" if spread else "-", reading.pitch_movement),
        _figure(pauses, reading.pauses),
    )


def _delivery_table(
    analytics: Mapping[str, Any], colors: Mapping[str, str]
) -> tuple[Block, ...]:
    delivery = analytics["delivery"]
    measured = [
        s
        for s in analytics["speakers"]
        if s["speaker_key"] in (delivery.get("speakers") or {})
    ]
    if not measured:
        return (
            _notice(
                "Nobody in this meeting had enough uninterrupted speech to measure delivery from."
            ),
        )
    talk_time = {
        key: float(figures.get("speech_ms") or 0)
        for key, figures in analytics["metrics"]["talk_time"].items()
    }
    readings = build_delivery_readings(
        delivery, talk_time, analytics.get("delivery_baselines")
    )
    table = Table(
        header=tuple(
            plain(label)
            for label in ("Speaker", "Pace", "Pitch", "Pitch movement", "Pauses")
        ),
        rows=tuple(
            _delivery_row(
                s, delivery["speakers"][s["speaker_key"]], readings[s["speaker_key"]]
            )
            for s in measured
        ),
        numeric=frozenset({1, 2, 3, 4}),
        row_colors=tuple(colors[s["speaker_key"]] for s in measured),
    )
    blocks: list[Block] = []
    if analytics.get("delivery_stale"):
        blocks.append(
            _notice("The transcript has changed since this was measured.", "warning")
        )
    blocks.append(table)
    if not delivery.get("cross_speaker_loudness_comparable", True):
        blocks.append(
            Paragraph(
                plain(
                    "These speakers reached the recording through different audio sources, so "
                    "how loud they sound reflects microphones and call quality rather than how "
                    "loudly they spoke. Loudness is not compared here for that reason."
                ),
                tone="muted",
            )
        )
    return tuple(blocks)


def delivery_blocks(
    analytics: Mapping[str, Any], colors: Mapping[str, str]
) -> tuple[Block, ...]:
    heading: tuple[Block, ...] = (
        Heading(1, plain("How people spoke")),
        _hint(
            "Measured from the audio. This describes how someone spoke, not how they felt."
        ),
    )
    absent = _delivery_status_blocks(analytics)
    if absent is not None:
        return heading + absent
    return (
        heading
        + _delivery_table(analytics, colors)
        + _note(
            "Pace is words a minute. The words beside it are anchored on measured English "
            "speech: recorded conversation runs at roughly 160 to 200 words a minute by this "
            "measure, presentations and narration slower. Treat them as a rough guide for a "
            "meeting held in another language.",
            "Pitch movement is the spread of someone's pitch, in semitones. Pauses are "
            "silences within a speaker's own turn, shown as a rate so a long contribution "
            "does not look like a hesitant one. Neither has a yardstick to measure against, "
            "so both are compared with the other people in this meeting and, where Nojoin "
            "has measured enough of a person's meetings, with their own usual figures.",
        )
    )


# -- the whole tab ------------------------------------------------------------


def analytics_blocks(
    analytics: Mapping[str, Any] | None, colors: Mapping[str, str]
) -> tuple[Block, ...]:
    """Every section of the Analytics tab, in the tab's order."""
    if not analytics or not analytics.get("speakers"):
        return (
            _notice(
                "No analytics yet. Analytics are built from the finished transcript. They "
                "appear once the meeting has been processed and speakers have been separated."
            ),
        )
    return (
        attribution_blocks(analytics)
        + headline_blocks(analytics)
        + who_spoke_blocks(analytics, colors)
        + who_spoke_when_blocks(analytics, colors)
        + turns_blocks(analytics, colors)
        + overlap_blocks(analytics)
        + delivery_blocks(analytics, colors)
        + ai_blocks(analytics, colors)
    )
