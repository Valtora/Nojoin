"""Measured overlapping speech, detected from the recording's audio.

Nojoin's transcripts hold no overlapping utterances in practice: the merge
assigns each transcribed segment one speaker from one mixed stream, so two
people talking at once is not representable in the transcript whatever
happened in the room. Interruption-style figures derived from the transcript
are therefore zero by construction on every recording class, and the only
honest source of overlap evidence is the audio itself.

This module reports *that and when* overlapping speech occurred, and how much
at minimum. It deliberately does not report who overlapped whom: per-event
"A interrupted B" claims fail three independent tests -- overlap detection
misses a share of events, speaker attribution inside overlap has no published
accuracy figure, and human annotators themselves agree on what counts as an
interruption at kappa ~0.31-0.35 -- so the surface language is "overlap",
never "interruption". The evidence is collected in docs/ANALYTICS_EVIDENCE.md,
including this exact procedure's measured accuracy on AMI ground truth:
precision 0.71-0.95 and recall 0.62-0.85 with a 250ms boundary collar, stable
on far-field audio and through 64kbps MP3, with totals running 74-88% of
truth -- an underestimate, which is why the total is presented as a floor.

Reuses ``pyannote/segmentation-3.0``, already a pipeline dependency for
boundary refinement, so this adds no model, no image growth, and no new
version surface beyond its own. Heavy imports stay inside functions.
"""

from __future__ import annotations

import logging
import math
from typing import Any

logger = logging.getLogger(__name__)

# Versioned like DELIVERY_METHOD_VERSION and for the same reason: a figure
# produced by one detection procedure is not comparable with one produced by
# another. Covers the model, the aggregation, and the thresholds below.
AUDIO_OVERLAP_METHOD_VERSION = 1

# Sliding-window step for the 10-second model windows. 2.5s gives each frame
# up to four votes, which is what lets the majority vote below smooth
# single-window flicker; it is the configuration the AMI validation measured.
OVERLAP_INFERENCE_STEP_S = 2.5

# Regions are stored for the density timeline; a cap keeps a pathological
# result from bloating the payload. Real meetings measured 25-460 regions.
MAX_STORED_REGIONS = 2000


def _overlap_regions_from_chunks(data, step_s: float, chunk_dur_s: float):
    """Overlap regions from chunk-wise powerset-decoded activations.

    ``data`` is (chunks, frames_per_chunk, local_speakers) with binary
    activations. Local speaker identities are not aligned across chunks, but
    the overlap indicator -- two or more active local speakers in a frame --
    is chunk-local, so it aggregates cleanly: average the indicator over
    every chunk covering a time point and take the majority.
    """
    import numpy as np

    n_chunks, frames_per_chunk, _ = data.shape
    chunk_overlap = (data >= 0.5).sum(axis=2) >= 2
    frame_s = chunk_dur_s / frames_per_chunk
    total_frames = int((n_chunks - 1) * step_s / frame_s) + frames_per_chunk
    votes = np.zeros(total_frames)
    counts = np.zeros(total_frames)
    for chunk in range(n_chunks):
        offset = int(round(chunk * step_s / frame_s))
        span = min(frames_per_chunk, total_frames - offset)
        votes[offset : offset + span] += chunk_overlap[chunk][:span]
        counts[offset : offset + span] += 1
    active = (votes / np.maximum(counts, 1)) >= 0.5

    regions: list[list[int]] = []
    start = None
    for index, flag in enumerate(active):
        t_ms = int(index * frame_s * 1000)
        if flag and start is None:
            start = t_ms
        elif not flag and start is not None:
            regions.append([start, t_ms])
            start = None
    if start is not None:
        regions.append([start, int(total_frames * frame_s * 1000)])
    return regions


def measure_audio_overlap(audio_path: str, hf_token: str | None) -> dict[str, Any]:
    """Detect overlapping-speech regions in a recording's audio."""
    import soundfile as sf
    import torch
    from pyannote.audio import Inference

    from backend.processing.segmentation_refinement import load_segmentation_model

    device_str = "cuda" if torch.cuda.is_available() else "cpu"
    model = load_segmentation_model(device_str, hf_token)
    inference = Inference(model, step=OVERLAP_INFERENCE_STEP_S)

    info = sf.info(audio_path)
    duration_ms = int(math.floor(info.frames / info.samplerate * 1000))

    scores = inference(audio_path)
    regions = _overlap_regions_from_chunks(scores.data, OVERLAP_INFERENCE_STEP_S, 10.0)
    total_ms = sum(end - start for start, end in regions)

    return {
        "method_version": AUDIO_OVERLAP_METHOD_VERSION,
        # Detection recall is under 1.0, so the total is a floor, and the
        # interface must present it as "at least".
        "total_overlap_ms": int(total_ms),
        "overlap_share_of_audio": (
            round(total_ms / duration_ms, 4) if duration_ms > 0 else 0.0
        ),
        "region_count": len(regions),
        "regions": regions[:MAX_STORED_REGIONS],
        "regions_truncated": len(regions) > MAX_STORED_REGIONS,
        "duration_ms": duration_ms,
    }
