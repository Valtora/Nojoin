"""Frame-level segmentation refinement (Phase F2/F3).

Runs the ``pyannote/segmentation-3.0`` model on individual utterance audio
spans to derive dense (~17 ms) speaker change-points and re-attribute words
through the existing canonical-pipeline splitter machinery. This is the
finalize-time safety net for the case where:

* Live VAD merged two speakers into one utterance, and
* The rolling-diarization window turns are too coarse for the Phase A
  splitters to fire.

Latency is intentionally not a goal here — accuracy is.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Sequence

import numpy as np
import torch

from backend.processing.embedding import cosine_similarity
from backend.processing.embedding_core import extract_embedding_for_segments

logger = logging.getLogger(__name__)

SEGMENTATION_MODEL = "pyannote/segmentation-3.0"
# Probability threshold for treating a frame as "this local speaker active".
SEGMENTATION_FRAME_ACTIVE_THRESHOLD = 0.5
# Reject noise-spike runs shorter than this.
SEGMENTATION_MIN_RUN_DURATION_MS = 250
# Bridge consecutive runs from the same local speaker separated by less than
# this gap (small silences inside a speaker's continuous talk).
SEGMENTATION_MIN_RUN_GAP_MS = 80
# F3 tie-break: cosine margin below which the best-vs-second-best recording
# speaker match is considered ambiguous and the centred-window re-embed runs.
SEGMENTATION_REFINEMENT_TIE_BREAK_MARGIN = 0.05
# Width (ms) of the centred window used by the F3 tie-breaker.
SEGMENTATION_TIE_BREAKER_WINDOW_MS = 1000
# Minimum cosine similarity for a local speaker to be linked to any recording
# speaker at all (otherwise the run is dropped from the synthetic turns).
SEGMENTATION_MIN_RECORDING_SPEAKER_MATCH = 0.30
# Skip utterances shorter than this — too little signal for the model.
SEGMENTATION_MIN_UTTERANCE_DURATION_MS = 600


_model_cache: dict[tuple[str, str], Any] = {}


@dataclass
class SegmentationTurnRow:
    """Lightweight stand-in for ``DiarizationWindowTurn`` that quacks at the
    canonical-pipeline splitters (they only read ``matched_recording_speaker_id``,
    ``start_ms``, ``end_ms``).
    """

    matched_recording_speaker_id: int | None
    start_ms: int
    end_ms: int


def release_segmentation_model_cache() -> None:
    """Drop cached segmentation models (mirrors ``diarize.release_pipeline_cache``)."""
    global _model_cache
    if _model_cache:
        logger.info("Releasing %s from segmentation model cache.", list(_model_cache.keys()))
        _model_cache.clear()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()


def _load_segmentation_model(device_str: str, hf_token: str | None):
    from pyannote.audio import Model

    cache_key = (SEGMENTATION_MODEL, device_str)
    cached = _model_cache.get(cache_key)
    if cached is not None:
        return cached
    model = Model.from_pretrained(SEGMENTATION_MODEL, use_auth_token=hf_token)
    model.to(torch.device(device_str))
    _model_cache[cache_key] = model
    return model


def _run_segmentation_inference(
    audio_path: str,
    *,
    span_start_s: float,
    span_end_s: float,
    device_str: str,
    hf_token: str | None,
) -> np.ndarray | None:
    """Returns a (num_frames, num_local_speakers) probability array for the span."""
    from pyannote.audio import Inference
    from pyannote.core import Segment

    model = _load_segmentation_model(device_str, hf_token)
    # NOTE: pyannote recommends sliding-window inference for the frame-based
    # segmentation-3.0 model — "whole" works but warns about accuracy /
    # memory issues on longer spans.
    inference = Inference(model, window="sliding", skip_aggregation=False)
    output = inference.crop(audio_path, Segment(span_start_s, span_end_s))
    if hasattr(output, "data"):
        data = np.asarray(output.data)
    else:
        data = np.asarray(output)
    if data.ndim == 1:
        data = data.reshape(-1, 1)
    return data


def _runs_from_frames(
    frames: np.ndarray,
    *,
    span_start_ms: int,
    span_end_ms: int,
) -> list[dict[str, int]]:
    """Convert per-frame multi-label activity into per-local-speaker active runs.

    Returns a list of ``{"local_speaker_idx", "start_ms", "end_ms"}`` dicts in
    absolute (recording-time) milliseconds, bridging tiny intra-speaker gaps
    and dropping runs shorter than ``SEGMENTATION_MIN_RUN_DURATION_MS``.
    """
    if frames.ndim != 2 or frames.size == 0:
        return []

    num_frames, num_speakers = frames.shape
    span_duration_ms = max(1, span_end_ms - span_start_ms)
    frame_duration_ms = span_duration_ms / float(num_frames)
    active = frames > SEGMENTATION_FRAME_ACTIVE_THRESHOLD

    runs: list[dict[str, int]] = []
    for local_idx in range(num_speakers):
        in_run = False
        run_start_frame = 0
        for frame_idx in range(num_frames):
            if active[frame_idx, local_idx]:
                if not in_run:
                    run_start_frame = frame_idx
                    in_run = True
            elif in_run:
                runs.append(
                    {
                        "local_speaker_idx": int(local_idx),
                        "start_ms": int(span_start_ms + run_start_frame * frame_duration_ms),
                        "end_ms": int(span_start_ms + frame_idx * frame_duration_ms),
                    }
                )
                in_run = False
        if in_run:
            runs.append(
                {
                    "local_speaker_idx": int(local_idx),
                    "start_ms": int(span_start_ms + run_start_frame * frame_duration_ms),
                    "end_ms": int(span_end_ms),
                }
            )

    # Bridge tiny gaps within the same local speaker.
    runs.sort(key=lambda r: (r["local_speaker_idx"], r["start_ms"]))
    bridged: list[dict[str, int]] = []
    for run in runs:
        if (
            bridged
            and bridged[-1]["local_speaker_idx"] == run["local_speaker_idx"]
            and run["start_ms"] - bridged[-1]["end_ms"] <= SEGMENTATION_MIN_RUN_GAP_MS
        ):
            bridged[-1]["end_ms"] = run["end_ms"]
            continue
        bridged.append(dict(run))

    return [
        run
        for run in bridged
        if (run["end_ms"] - run["start_ms"]) >= SEGMENTATION_MIN_RUN_DURATION_MS
    ]


def _best_match_to_recording_speakers(
    embedding: list[float] | None,
    recording_speakers: Sequence[Any],
) -> tuple[Any | None, Any | None, float, float]:
    if embedding is None:
        return None, None, 0.0, 0.0
    scored: list[tuple[Any, float]] = []
    for speaker in recording_speakers:
        candidate_embedding = getattr(speaker, "embedding", None)
        if not candidate_embedding:
            continue
        score = float(cosine_similarity(embedding, candidate_embedding))
        scored.append((speaker, score))
    if not scored:
        return None, None, 0.0, 0.0
    scored.sort(key=lambda item: item[1], reverse=True)
    best, best_score = scored[0]
    second, second_score = scored[1] if len(scored) > 1 else (None, 0.0)
    return best, second, float(best_score), float(second_score)


def _match_local_speaker(
    audio_path: str,
    *,
    runs_for_local: Sequence[dict[str, int]],
    recording_speakers: Sequence[Any],
    device_str: str,
    hf_token: str | None,
) -> tuple[Any | None, float]:
    """Cosine-match the aggregated embedding of a local speaker's runs to a
    recording speaker. Applies the F3 tie-breaker when the best/second margin
    is below ``SEGMENTATION_REFINEMENT_TIE_BREAK_MARGIN``.
    """
    sorted_runs = sorted(
        runs_for_local,
        key=lambda r: r["end_ms"] - r["start_ms"],
        reverse=True,
    )
    aggregate_segments = [
        (run["start_ms"] / 1000.0, run["end_ms"] / 1000.0) for run in sorted_runs[:3]
    ]
    if not aggregate_segments:
        return None, 0.0
    embedding = extract_embedding_for_segments(
        audio_path,
        aggregate_segments,
        device_str=device_str,
        hf_token=hf_token,
    )
    best, second, best_score, second_score = _best_match_to_recording_speakers(
        embedding, recording_speakers
    )
    if best is None or best_score < SEGMENTATION_MIN_RECORDING_SPEAKER_MATCH:
        return None, best_score

    margin = best_score - second_score
    if second is not None and margin < SEGMENTATION_REFINEMENT_TIE_BREAK_MARGIN:
        # F3: pull a centred ~1 s embedding from the longest run and re-score.
        longest = sorted_runs[0]
        midpoint_ms = (longest["start_ms"] + longest["end_ms"]) / 2.0
        half_ms = SEGMENTATION_TIE_BREAKER_WINDOW_MS / 2.0
        tb_start_s = max(longest["start_ms"], midpoint_ms - half_ms) / 1000.0
        tb_end_s = min(longest["end_ms"], midpoint_ms + half_ms) / 1000.0
        if tb_end_s - tb_start_s >= 0.5:
            tb_embedding = extract_embedding_for_segments(
                audio_path,
                [(tb_start_s, tb_end_s)],
                device_str=device_str,
                hf_token=hf_token,
            )
            tb_best, _, tb_score, _ = _best_match_to_recording_speakers(
                tb_embedding, recording_speakers
            )
            if tb_best is not None and tb_score >= best_score:
                best = tb_best
                best_score = tb_score
    return best, float(best_score)


def refine_utterance_via_segmentation(
    audio_path: str,
    *,
    utterance,
    recording_speakers: Sequence[Any],
    device_str: str,
    hf_token: str | None,
) -> list[SegmentationTurnRow]:
    """Build synthetic turn rows for ``utterance`` from frame-level segmentation.

    Returns an empty list when the model cannot identify at least two
    distinct recording speakers inside the utterance span — in which case
    the caller should leave the utterance untouched.
    """
    if not recording_speakers:
        return []

    span_start_ms = int(utterance.start_ms)
    span_end_ms = int(utterance.end_ms)
    if span_end_ms - span_start_ms < SEGMENTATION_MIN_UTTERANCE_DURATION_MS:
        return []

    try:
        frames = _run_segmentation_inference(
            audio_path,
            span_start_s=span_start_ms / 1000.0,
            span_end_s=span_end_ms / 1000.0,
            device_str=device_str,
            hf_token=hf_token,
        )
    except Exception as exc:
        logger.warning(
            "Segmentation inference failed for utterance %s: %s",
            getattr(utterance, "public_id", "?"),
            exc,
            exc_info=True,
        )
        return []

    if frames is None or not isinstance(frames, np.ndarray) or frames.size == 0:
        return []

    runs = _runs_from_frames(
        frames, span_start_ms=span_start_ms, span_end_ms=span_end_ms
    )
    if not runs:
        return []

    runs_by_local: dict[int, list[dict[str, int]]] = {}
    for run in runs:
        runs_by_local.setdefault(int(run["local_speaker_idx"]), []).append(run)

    local_to_recording_speaker_id: dict[int, int] = {}
    for local_idx, local_runs in runs_by_local.items():
        best, _score = _match_local_speaker(
            audio_path,
            runs_for_local=local_runs,
            recording_speakers=recording_speakers,
            device_str=device_str,
            hf_token=hf_token,
        )
        if best is None or getattr(best, "id", None) is None:
            continue
        local_to_recording_speaker_id[int(local_idx)] = int(best.id)

    if len(set(local_to_recording_speaker_id.values())) < 2:
        return []

    turn_rows = [
        SegmentationTurnRow(
            matched_recording_speaker_id=local_to_recording_speaker_id[
                int(run["local_speaker_idx"])
            ],
            start_ms=int(run["start_ms"]),
            end_ms=int(run["end_ms"]),
        )
        for run in runs
        if int(run["local_speaker_idx"]) in local_to_recording_speaker_id
    ]
    turn_rows.sort(key=lambda row: (row.start_ms, row.end_ms))
    return turn_rows
