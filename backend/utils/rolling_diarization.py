from __future__ import annotations

import hashlib
from typing import Any, Mapping

from sqlmodel import select

from backend.models.pipeline import (
    DiarizationWindowResult,
    DiarizationWindowTurn,
    RecordingAudioWindowManifest,
)


DEFAULT_ROLLING_DIARIZATION_MODEL = "pyannote/speaker-diarization-community-1"
MIN_ROLLING_DIARIZATION_EMBEDDING_DURATION_S = 0.5
ROLLING_DIARIZATION_OVERLAP_EPSILON_S = 0.05


def get_rolling_diarization_model_name() -> str:
    return DEFAULT_ROLLING_DIARIZATION_MODEL


def get_rolling_diarization_model_version(
    model_name: str = DEFAULT_ROLLING_DIARIZATION_MODEL,
) -> str | None:
    cleaned_name = str(model_name or "").strip()
    if not cleaned_name:
        return None

    tail = cleaned_name.rsplit("/", 1)[-1]
    prefix = "speaker-diarization-"
    if tail.startswith(prefix):
        return tail[len(prefix) :] or None
    return tail or None


def build_rolling_diarization_config_hash(
    config: Mapping[str, Any],
    *,
    target_window_ms: int | None = None,
    hop_ms: int | None = None,
) -> str:
    digest_source = "|".join(
        [
            str(config.get("enable_rolling_diarization", True)),
            str(config.get("enable_diarization", True)),
            str(config.get("processing_device", "auto")),
            str(bool(config.get("use_gpu", True))),
            str(target_window_ms if target_window_ms is not None else config.get("rolling_diarization_window_ms", 20_000)),
            str(hop_ms if hop_ms is not None else config.get("rolling_diarization_hop_ms", 5_000)),
            get_rolling_diarization_model_name(),
        ]
    )
    return hashlib.sha256(digest_source.encode("utf-8")).hexdigest()


def build_diarization_window_payload(
    diarization_result,
    *,
    window_start_ms: int,
    window_end_ms: int,
    speaker_metadata_by_key: Mapping[str, dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    speaker_labels: set[str] = set()
    turn_payloads: list[dict[str, Any]] = []

    if diarization_result is not None:
        for turn, track, label in diarization_result.itertracks(yield_label=True):
            local_speaker_key = str(label)
            speaker_labels.add(local_speaker_key)
            turn_payloads.append(
                {
                    "local_speaker_key": local_speaker_key,
                    "start_ms": int(round(window_start_ms + (float(turn.start) * 1000.0))),
                    "end_ms": int(round(window_start_ms + (float(turn.end) * 1000.0))),
                    "track": str(track),
                }
            )

    payload: dict[str, Any] = {
        "window_start_ms": int(window_start_ms),
        "window_end_ms": int(window_end_ms),
        "speaker_labels": sorted(speaker_labels),
        "turn_count": len(turn_payloads),
        "turns": turn_payloads,
    }
    if speaker_metadata_by_key:
        payload["speaker_metadata"] = {
            str(local_speaker_key): dict(metadata or {})
            for local_speaker_key, metadata in speaker_metadata_by_key.items()
        }

    return payload, turn_payloads


def persist_diarization_window_result(
    session,
    *,
    recording_id: int,
    manifest_row: RecordingAudioWindowManifest,
    processing_run_id: int | None,
    diarization_result,
    config_hash: str,
    device: str,
    model_name: str = DEFAULT_ROLLING_DIARIZATION_MODEL,
    model_version: str | None = None,
    error_message: str | None = None,
    speaker_metadata_by_key: Mapping[str, dict[str, Any]] | None = None,
) -> DiarizationWindowResult:
    existing_statement = (
        select(DiarizationWindowResult)
        .where(DiarizationWindowResult.recording_id == recording_id)
        .where(DiarizationWindowResult.window_index == manifest_row.window_index)
        .where(DiarizationWindowResult.processing_run_id == processing_run_id)
    )
    if hasattr(session, "exec"):
        existing_result = session.exec(existing_statement).first()
    else:
        existing_result = session.execute(existing_statement).scalars().first()

    if existing_result is None:
        existing_result = DiarizationWindowResult(
            recording_id=recording_id,
            processing_run_id=processing_run_id,
            window_index=manifest_row.window_index,
            window_start_ms=manifest_row.window_start_ms,
            window_end_ms=manifest_row.window_end_ms,
            chunk_start_sequence=manifest_row.chunk_start_sequence,
            chunk_end_sequence=manifest_row.chunk_end_sequence,
        )
    else:
        turn_statement = select(DiarizationWindowTurn).where(
            DiarizationWindowTurn.window_result_id == existing_result.id
        )
        if hasattr(session, "exec"):
            existing_turn_rows = session.exec(turn_statement).all()
        else:
            existing_turn_rows = session.execute(turn_statement).scalars().all()
        for turn_row in existing_turn_rows:
            session.delete(turn_row)

    payload, turn_payloads = build_diarization_window_payload(
        diarization_result,
        window_start_ms=int(manifest_row.window_start_ms),
        window_end_ms=int(manifest_row.window_end_ms),
        speaker_metadata_by_key=speaker_metadata_by_key,
    )
    if error_message:
        payload["error"] = error_message

    existing_result.processing_run_id = processing_run_id
    existing_result.window_start_ms = manifest_row.window_start_ms
    existing_result.window_end_ms = manifest_row.window_end_ms
    existing_result.chunk_start_sequence = manifest_row.chunk_start_sequence
    existing_result.chunk_end_sequence = manifest_row.chunk_end_sequence
    existing_result.model_name = model_name
    existing_result.model_version = model_version or get_rolling_diarization_model_version(model_name)
    existing_result.device = device
    existing_result.config_hash = config_hash
    existing_result.status = "failed" if error_message else "completed"
    existing_result.raw_payload = payload
    session.add(existing_result)
    session.flush()

    for turn_payload in turn_payloads:
        metadata_payload = {
            "track": str(turn_payload["track"]),
        }
        session.add(
            DiarizationWindowTurn(
                window_result_id=existing_result.id,
                local_speaker_key=str(turn_payload["local_speaker_key"]),
                start_ms=int(turn_payload["start_ms"]),
                end_ms=int(turn_payload["end_ms"]),
                confidence=None,
                matched_recording_speaker_id=None,
                metadata_payload=metadata_payload,
            )
        )

    return existing_result


def build_window_speaker_metadata(
    *,
    diarization_result,
    audio_path: str,
    device_str: str,
    hf_token: str | None,
    recording_speakers: list[Any],
    global_speakers: list[Any],
    window_start_ms: int | None = None,
) -> dict[str, dict[str, Any]]:
    metadata_by_key, _ = analyze_window_speakers(
        diarization_result=diarization_result,
        audio_path=audio_path,
        device_str=device_str,
        hf_token=hf_token,
        recording_speakers=recording_speakers,
        global_speakers=global_speakers,
        window_start_ms=window_start_ms,
    )
    return metadata_by_key


def _spans_overlap(
    left_span: tuple[float, float],
    right_span: tuple[float, float],
    *,
    epsilon_s: float = ROLLING_DIARIZATION_OVERLAP_EPSILON_S,
) -> bool:
    left_start, left_end = left_span
    right_start, right_end = right_span
    return min(left_end, right_end) - max(left_start, right_start) > epsilon_s


def _select_clean_speaker_spans(
    spans_by_speaker: Mapping[str, list[tuple[float, float]]],
    *,
    local_speaker_key: str,
) -> list[tuple[float, float]]:
    speaker_spans = spans_by_speaker.get(local_speaker_key, [])
    clean_spans: list[tuple[float, float]] = []

    for span in sorted(
        speaker_spans,
        key=lambda candidate: candidate[1] - candidate[0],
        reverse=True,
    ):
        span_duration_s = max(span[1] - span[0], 0.0)
        if span_duration_s < MIN_ROLLING_DIARIZATION_EMBEDDING_DURATION_S:
            continue

        overlaps_other_speaker = False
        for other_speaker_key, other_spans in spans_by_speaker.items():
            if other_speaker_key == local_speaker_key:
                continue
            if any(_spans_overlap(span, other_span) for other_span in other_spans):
                overlaps_other_speaker = True
                break

        if not overlaps_other_speaker:
            clean_spans.append(span)

    clean_spans.sort(key=lambda candidate: candidate[0])
    return clean_spans


def analyze_window_speakers(
    *,
    diarization_result,
    audio_path: str,
    device_str: str,
    hf_token: str | None,
    recording_speakers: list[Any],
    global_speakers: list[Any],
    window_start_ms: int | None = None,
) -> tuple[dict[str, dict[str, Any]], dict[str, list[float]]]:
    if diarization_result is None:
        return {}, {}

    from backend.processing.embedding import (
        IDENTIFICATION_THRESHOLD,
        MARGIN_OF_VICTORY,
        cosine_similarity,
    )
    from backend.processing.embedding_core import extract_embedding_for_segments

    spans_by_speaker: dict[str, list[tuple[float, float]]] = {}
    for turn, _track, label in diarization_result.itertracks(yield_label=True):
        start_s = float(turn.start)
        end_s = float(turn.end)
        if end_s <= start_s:
            continue
        spans_by_speaker.setdefault(str(label), []).append((start_s, end_s))

    metadata_by_key: dict[str, dict[str, Any]] = {}
    embeddings_by_key: dict[str, list[float]] = {}
    for local_speaker_key, spans in spans_by_speaker.items():
        total_duration_s = sum(max(end_s - start_s, 0.0) for start_s, end_s in spans)
        clean_spans = _select_clean_speaker_spans(
            spans_by_speaker,
            local_speaker_key=local_speaker_key,
        )
        clean_duration_s = sum(
            max(end_s - start_s, 0.0) for start_s, end_s in clean_spans
        )
        metadata: dict[str, Any] = {
            "segment_count": len(spans),
            "total_duration_ms": int(round(total_duration_s * 1000.0)),
            "clean_segment_count": len(clean_spans),
            "clean_duration_ms": int(round(clean_duration_s * 1000.0)),
            "source_spans_ms": [
                {
                    "start_ms": int((window_start_ms or 0) + round(start_s * 1000.0)),
                    "end_ms": int((window_start_ms or 0) + round(end_s * 1000.0)),
                }
                for start_s, end_s in clean_spans
            ],
            "embedding_available": False,
        }
        if clean_duration_s < MIN_ROLLING_DIARIZATION_EMBEDDING_DURATION_S:
            metadata_by_key[local_speaker_key] = metadata
            continue

        try:
            embedding = extract_embedding_for_segments(
                audio_path,
                clean_spans,
                device_str=device_str,
                hf_token=hf_token,
            )
        except Exception:
            embedding = None
        if not embedding:
            metadata_by_key[local_speaker_key] = metadata
            continue

        metadata["embedding_available"] = True
        embeddings_by_key[local_speaker_key] = embedding

        best_recording_speaker = None
        best_recording_score = 0.0
        second_recording_score = 0.0
        for recording_speaker in recording_speakers:
            if not getattr(recording_speaker, "embedding", None):
                continue
            score = cosine_similarity(embedding, recording_speaker.embedding)
            if score > best_recording_score:
                second_recording_score = best_recording_score
                best_recording_score = score
                best_recording_speaker = recording_speaker
            elif score > second_recording_score:
                second_recording_score = score

        if (
            best_recording_speaker is not None
            and best_recording_score >= IDENTIFICATION_THRESHOLD
            and (best_recording_score - second_recording_score) >= MARGIN_OF_VICTORY
        ):
            metadata["best_recording_speaker_id"] = int(best_recording_speaker.id)
        if best_recording_speaker is not None:
            metadata["best_recording_speaker_score"] = round(float(best_recording_score), 4)

        best_global_speaker = None
        best_global_score = 0.0
        second_global_score = 0.0
        for global_speaker in global_speakers:
            if not getattr(global_speaker, "embedding", None):
                continue
            score = cosine_similarity(embedding, global_speaker.embedding)
            if score > best_global_score:
                second_global_score = best_global_score
                best_global_score = score
                best_global_speaker = global_speaker
            elif score > second_global_score:
                second_global_score = score

        if (
            best_global_speaker is not None
            and best_global_score >= IDENTIFICATION_THRESHOLD
            and (best_global_score - second_global_score) >= MARGIN_OF_VICTORY
        ):
            metadata["best_global_speaker_id"] = int(best_global_speaker.id)
        if best_global_speaker is not None:
            metadata["best_global_speaker_score"] = round(float(best_global_score), 4)

        metadata_by_key[local_speaker_key] = metadata

    return metadata_by_key, embeddings_by_key