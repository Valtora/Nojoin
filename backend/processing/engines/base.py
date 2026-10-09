from abc import ABC, abstractmethod


class TranscriptionEngine(ABC):
    """Base of a pluggable transcription engine.

    transcribe() returns the canonical schema the rest of the pipeline expects
    (consumed by backend/utils/transcript_utils.py:combine_transcription_diarization):

        {
          "text": str,                 # full transcript (-> transcript.text)
          "language": str,             # optional; logged only
          "segments": [
            {"start": float, "end": float, "text": str,
             "words": [{"start": float, "end": float, "word": str}]}  # words optional
          ]
        }

    A result with empty text and no segments is a success: the audio held no
    speech. Any failure, a missing file included, raises TranscriptionError
    (backend/processing/engines/errors.py) instead, so callers never mistake a
    crash for silence.
    """

    name: str

    @abstractmethod
    def transcribe(self, audio_path: str, config: dict) -> dict: ...

    def release(self) -> None:
        """Release cached models / VRAM. Default no-op."""
        return None
