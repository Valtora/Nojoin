"""A stand-in for the live label models, driven by the audio itself.

Each audio sample holds the id of the speaker talking at that moment (0 is
silence), so a test writes a meeting as a list of turns. The fake segmentation
gives each speaker in a window a local slot, in order of appearance, active on
the frames where that speaker talks. The fake embedding is a fixed unit vector
per speaker: random 256-dimensional vectors sit at a cosine distance near 1, so
speakers separate as clearly as real voices that do not sound alike.
"""

from __future__ import annotations

import numpy as np

SAMPLE_RATE = 16000
FRAMES = 293  # segmentation-3.0's frame count for a 5 s window
LOCAL_SPEAKERS = 3


def speaker_audio(turns: list[tuple[float, float, int]], total_s: float) -> np.ndarray:
    """Mono audio of ``total_s`` seconds where each turn's samples hold its speaker id."""
    audio = np.zeros(int(round(total_s * SAMPLE_RATE)), np.float32)
    for start, end, speaker in turns:
        audio[int(round(start * SAMPLE_RATE)) : int(round(end * SAMPLE_RATE))] = speaker
    return audio


class SpeakerTrackModel:
    """Callable like ``LiveLabelModel``: windows in, activations and embeddings out."""

    def __init__(self, dim: int = 256, speakers: int = 32, seed: int = 7) -> None:
        rng = np.random.default_rng(seed)
        voices = rng.normal(size=(speakers + 1, dim))
        self.voices = (voices / np.linalg.norm(voices, axis=1, keepdims=True)).astype(
            np.float32
        )
        self.calls = 0
        self.windows = 0

    def __call__(self, windows: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        self.calls += 1
        self.windows += len(windows)
        batch, samples = windows.shape
        centres = ((np.arange(FRAMES) + 0.5) * samples / FRAMES).astype(int)
        segmentation = np.zeros((batch, FRAMES, LOCAL_SPEAKERS), np.float32)
        # Inactive local slots still get an embedding, as the real model gives.
        embeddings = np.repeat(self.voices[:1][None], batch * LOCAL_SPEAKERS, 0)
        embeddings = embeddings.reshape(batch, LOCAL_SPEAKERS, -1).copy()
        for b in range(batch):
            ids = np.rint(windows[b, centres]).astype(int)
            order = [int(k) for k in dict.fromkeys(ids.tolist()) if k > 0]
            for local, speaker in enumerate(order[:LOCAL_SPEAKERS]):
                segmentation[b, :, local] = ids == speaker
                embeddings[b, local] = self.voices[speaker]
        return segmentation, embeddings
