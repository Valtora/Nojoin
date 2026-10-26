"""Audio buffering and source-channel evidence for live transcription."""

import os
from dataclasses import dataclass
from typing import Any

from backend.processing.browser_live_audio import (
    BROWSER_LIVE_MICROPHONE_SOURCE,
    BROWSER_LIVE_SAMPLE_RATE_HZ,
    BROWSER_LIVE_SOURCE_NAME_BY_CHANNEL,
    BROWSER_LIVE_SYSTEM_SOURCE,
    LIVE_SOURCE_AUTHORITY_CLEAR,
    LIVE_SOURCE_AUTHORITY_NONE,
    LIVE_SOURCE_AUTHORITY_OVERLAP,
)

# Sample rate of the canonical browser live-capture WAV.
LIVE_SAMPLE_RATE = BROWSER_LIVE_SAMPLE_RATE_HZ
LIVE_SOURCE_DOMINANT_SHARE_THRESHOLD = 0.65
LIVE_SOURCE_DOMINANCE_RATIO_THRESHOLD = 1.5
LIVE_SOURCE_OVERLAP_SHARE_THRESHOLD = 0.25


@dataclass(frozen=True)
class LiveSourceChannelEvidence:
    dominant_source: str | None = None
    primary_source: str | None = None
    primary_share: float | None = None
    secondary_share: float | None = None
    source_overlap: bool = False
    authority: str = LIVE_SOURCE_AUTHORITY_NONE
    reason: str = "no_source_activity"
    preferred_label: str | None = None
    excluded_labels: tuple[str, ...] = ()
    speaker_confidence: float | None = None

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "authority": self.authority,
            "reason": self.reason,
            "dominant_source": self.dominant_source,
            "primary_source": self.primary_source,
            "source_overlap": self.source_overlap,
            "preferred_label": self.preferred_label,
            "excluded_labels": list(self.excluded_labels),
            "speaker_confidence": self.speaker_confidence,
        }
        if self.primary_share is not None:
            payload["primary_share"] = round(float(self.primary_share), 4)
        if self.secondary_share is not None:
            payload["secondary_share"] = round(float(self.secondary_share), 4)
        return payload


def _ensure_channel_first(audio):
    if audio.ndim == 1:
        return audio.unsqueeze(0)
    return audio


def _expand_audio_channels(audio, target_channel_count: int):
    import torch

    audio = _ensure_channel_first(audio)
    channel_count = int(audio.size(0))
    if channel_count == target_channel_count:
        return audio
    if channel_count == 1 and target_channel_count > 1:
        return audio.repeat(target_channel_count, 1)
    if channel_count > target_channel_count:
        return audio[:target_channel_count]
    padding = audio[-1:, :].repeat(target_channel_count - channel_count, 1)
    return torch.cat([audio, padding], dim=0)


def _concat_live_audio_channels(parts: list):
    import torch

    if not parts:
        return torch.zeros((1, 0))
    normalized_parts = [_ensure_channel_first(part) for part in parts]
    target_channel_count = max(int(part.size(0)) for part in normalized_parts)
    return torch.cat(
        [
            _expand_audio_channels(part, target_channel_count)
            for part in normalized_parts
        ],
        dim=1,
    )


def _mix_live_audio_channels(audio):
    audio = _ensure_channel_first(audio)
    if audio.size(0) == 1:
        return audio.squeeze(0)
    return audio.mean(dim=0)


def _read_live_audio_channels(path: str):
    from backend.processing.vad import safe_read_audio

    return _ensure_channel_first(
        safe_read_audio(path, sampling_rate=LIVE_SAMPLE_RATE, preserve_channels=True)
    )


def _analyze_live_source_channels(audio) -> dict[str, Any]:
    import torch

    audio = _ensure_channel_first(audio)
    channel_count = int(audio.size(0))
    sample_count = int(audio.size(1)) if audio.ndim > 1 else int(audio.numel())
    payload: dict[str, Any] = {
        "channel_count": channel_count,
        "sample_count": sample_count,
    }
    if channel_count < 2 or sample_count <= 0:
        return payload

    rms = torch.sqrt(torch.mean(audio.float() * audio.float(), dim=1) + 1e-12)
    total_rms = float(torch.sum(rms).item())
    if total_rms <= 1e-8:
        payload.update(
            {
                "channel_rms": [0.0 for _ in range(channel_count)],
                "channel_shares": [0.0 for _ in range(channel_count)],
                "source_overlap": False,
            }
        )
        return payload

    shares = [float(value.item()) / total_rms for value in rms]
    primary_channel = int(torch.argmax(rms).item())
    sorted_shares = sorted(shares, reverse=True)
    primary_share = sorted_shares[0]
    secondary_share = sorted_shares[1] if len(sorted_shares) > 1 else 0.0
    primary_source = BROWSER_LIVE_SOURCE_NAME_BY_CHANNEL.get(
        primary_channel, f"channel_{primary_channel}"
    )
    dominant_source = None
    if primary_share >= LIVE_SOURCE_DOMINANT_SHARE_THRESHOLD and (
        secondary_share <= 0.0
        or primary_share / secondary_share >= LIVE_SOURCE_DOMINANCE_RATIO_THRESHOLD
    ):
        dominant_source = primary_source

    payload.update(
        {
            "channel_rms": [round(float(value.item()), 6) for value in rms],
            "channel_shares": [round(share, 4) for share in shares],
            "primary_channel": primary_channel,
            "primary_source": primary_source,
            "primary_share": round(primary_share, 4),
            "secondary_share": round(secondary_share, 4),
            "dominant_source": dominant_source,
            "source_overlap": bool(
                secondary_share >= LIVE_SOURCE_OVERLAP_SHARE_THRESHOLD
            ),
        }
    )
    return payload


def _source_channel_speaker_confidence(source_activity: dict[str, Any]) -> float | None:
    if not source_activity.get("dominant_source"):
        return None
    primary_share = float(source_activity.get("primary_share") or 0.0)
    if source_activity.get("source_overlap"):
        return round(min(primary_share, 0.54), 4)
    return round(max(primary_share, LIVE_SOURCE_DOMINANT_SHARE_THRESHOLD), 4)


def _coerce_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_live_source_channel_evidence(
    source_channel_evidence: LiveSourceChannelEvidence | dict[str, Any] | None,
) -> LiveSourceChannelEvidence | None:
    if source_channel_evidence is None:
        return None
    if isinstance(source_channel_evidence, LiveSourceChannelEvidence):
        return source_channel_evidence
    if not isinstance(source_channel_evidence, dict):
        return None
    return LiveSourceChannelEvidence(
        dominant_source=source_channel_evidence.get("dominant_source"),
        primary_source=source_channel_evidence.get("primary_source"),
        primary_share=_coerce_float(source_channel_evidence.get("primary_share")),
        secondary_share=_coerce_float(source_channel_evidence.get("secondary_share")),
        source_overlap=bool(source_channel_evidence.get("source_overlap", False)),
        authority=str(
            source_channel_evidence.get("authority") or LIVE_SOURCE_AUTHORITY_NONE
        ),
        reason=str(source_channel_evidence.get("reason") or "source_channel_payload"),
        preferred_label=source_channel_evidence.get("preferred_label"),
        excluded_labels=tuple(
            str(label)
            for label in source_channel_evidence.get("excluded_labels", [])
            if label
        ),
        speaker_confidence=_coerce_float(
            source_channel_evidence.get("speaker_confidence")
        ),
    )


def _build_live_source_channel_evidence(
    source_activity: dict[str, Any],
    source_channel_labels: dict[str, str],
) -> LiveSourceChannelEvidence:
    dominant_source = source_activity.get("dominant_source")
    primary_source = source_activity.get("primary_source")
    primary_share = _coerce_float(source_activity.get("primary_share"))
    secondary_share = _coerce_float(source_activity.get("secondary_share"))
    source_overlap = bool(source_activity.get("source_overlap", False))
    speaker_confidence = _source_channel_speaker_confidence(source_activity)

    if source_overlap:
        return LiveSourceChannelEvidence(
            dominant_source=dominant_source,
            primary_source=primary_source,
            primary_share=primary_share,
            secondary_share=secondary_share,
            source_overlap=True,
            authority=LIVE_SOURCE_AUTHORITY_OVERLAP,
            reason="overlap_reduces_source_authority",
            speaker_confidence=speaker_confidence,
        )

    if not dominant_source:
        return LiveSourceChannelEvidence(
            dominant_source=None,
            primary_source=primary_source,
            primary_share=primary_share,
            secondary_share=secondary_share,
            source_overlap=False,
            authority=LIVE_SOURCE_AUTHORITY_NONE,
            reason="no_clear_source_dominance",
            speaker_confidence=None,
        )

    if dominant_source == BROWSER_LIVE_MICROPHONE_SOURCE:
        preferred_label = source_channel_labels.get(BROWSER_LIVE_MICROPHONE_SOURCE)
        return LiveSourceChannelEvidence(
            dominant_source=dominant_source,
            primary_source=primary_source,
            primary_share=primary_share,
            secondary_share=secondary_share,
            source_overlap=False,
            authority=LIVE_SOURCE_AUTHORITY_CLEAR,
            reason=(
                "microphone_dominant_preferred_label"
                if preferred_label
                else "microphone_dominant_assignable"
            ),
            preferred_label=preferred_label,
            speaker_confidence=speaker_confidence,
        )

    if dominant_source == BROWSER_LIVE_SYSTEM_SOURCE:
        microphone_label = source_channel_labels.get(BROWSER_LIVE_MICROPHONE_SOURCE)
        return LiveSourceChannelEvidence(
            dominant_source=dominant_source,
            primary_source=primary_source,
            primary_share=primary_share,
            secondary_share=secondary_share,
            source_overlap=False,
            authority=LIVE_SOURCE_AUTHORITY_CLEAR,
            reason=(
                "system_dominant_excludes_microphone"
                if microphone_label
                else "system_dominant_no_known_microphone"
            ),
            excluded_labels=(str(microphone_label),) if microphone_label else (),
            speaker_confidence=speaker_confidence,
        )

    return LiveSourceChannelEvidence(
        dominant_source=dominant_source,
        primary_source=primary_source,
        primary_share=primary_share,
        secondary_share=secondary_share,
        source_overlap=False,
        authority=LIVE_SOURCE_AUTHORITY_CLEAR,
        reason="source_dominant",
        speaker_confidence=speaker_confidence,
    )


def _build_live_combined_buffer(
    *,
    temp_dir,
    live_dir,
    buffer_path: str,
    run: list[int],
):
    """Concatenate the carried buffer and the drained run into one combined clip.

    Returns ``(combined_channels, combined, combined_len, combined_source_activity)``
    where ``combined`` is the mono mix used for VAD/ASR and ``combined_channels``
    preserves per-source channels for source-channel attribution.
    """
    channel_parts = []
    if os.path.exists(buffer_path):
        channel_parts.append(_read_live_audio_channels(buffer_path))
    for seg_n in run:
        channel_parts.append(_read_live_audio_channels(str(temp_dir / f"{seg_n}.wav")))

    combined_channels = _concat_live_audio_channels(channel_parts)
    combined = _mix_live_audio_channels(combined_channels)
    combined_len = combined.numel() / LIVE_SAMPLE_RATE
    combined_source_activity = _analyze_live_source_channels(combined_channels)
    return combined_channels, combined, combined_len, combined_source_activity


def _carry_over_live_buffer(
    *,
    buffer_path: str,
    context_path: str,
    combined_channels,
    combined_abs_start: float,
    cut_point: float,
    prev_context_channels,
    context_window_samples: int,
) -> float:
    """Persist the unconsumed trailing audio and refresh the left-context buffer.

    Writes the carry-over buffer (audio past ``cut_point``) and the rolling
    left-context run-up (the last ``context_window_samples`` of consumed audio),
    removing either file when its content is empty. Returns the new
    ``buffer_abs_start`` for the next run.
    """
    from backend.processing.vad import silero_vad

    W = context_window_samples
    cut_sample = int(cut_point * LIVE_SAMPLE_RATE)
    new_buffer_channels = combined_channels[:, cut_sample:]
    if new_buffer_channels.numel() > 0:
        tensor = (
            new_buffer_channels
            if new_buffer_channels.ndim > 1
            else new_buffer_channels.unsqueeze(0)
        )
        silero_vad.save_audio(buffer_path, tensor, sampling_rate=LIVE_SAMPLE_RATE)
    elif os.path.exists(buffer_path):
        try:
            os.remove(buffer_path)
        except OSError:
            pass
    new_abs_start = combined_abs_start + cut_point

    # consumed = the already-consumed audio immediately preceding the new
    # buffer; its last W samples become run-up for the next run.
    consumed_channels = _concat_live_audio_channels(
        [prev_context_channels, combined_channels[:, :cut_sample]]
    )
    if W > 0:
        consumed_channels = consumed_channels[:, -W:]
    else:
        consumed_channels = consumed_channels[:, :0]
    if consumed_channels.numel() > 0:
        tensor = (
            consumed_channels
            if consumed_channels.ndim > 1
            else consumed_channels.unsqueeze(0)
        )
        silero_vad.save_audio(context_path, tensor, sampling_rate=LIVE_SAMPLE_RATE)
    elif os.path.exists(context_path):
        try:
            os.remove(context_path)
        except OSError:
            pass

    return new_abs_start
