from abc import ABC, abstractmethod

# Config key the finalize pipeline sets on the config it hands an engine: True
# when the audio the engine reads is VAD-muted, so every sample outside the
# speech VAD kept is an exact zero. With VAD on but transcription unmuted
# (vad_mute_transcription) it is False. An engine may rely on the zeros only
# when it is set; the live lane never sets it.
VAD_MUTED_INPUT = "vad_muted_input"

# Config key the finalize pipeline sets when VAD ran: the path of the VAD-muted
# file, where every sample outside speech is an exact zero. An engine may detect
# an automatic language on this file's speech rather than on the start of the
# file it reads (#671). Unset with VAD off and in the live lane.
LANGUAGE_DETECTION_AUDIO = "language_detection_audio"

# Config key the live lane's settling steps set once a capture's automatic
# language is known (#776): the code Whisper detected on that capture's earlier
# steps. On automatic, Whisper transcribes in it instead of detecting again; a
# set language still wins. Unset in the finalize pipeline.
CAPTURE_LANGUAGE = "capture_language"


class TranscriptionEngine(ABC):
    """Base of a pluggable transcription engine.

    transcribe() returns raw native recognition in the canonical schema.
    Call processing.transcribe.transcribe_audio() for the complete pipeline,
    including forced alignment for engines without native word timestamps.
    The schema the rest of the pipeline expects
    (consumed by backend/utils/transcript_utils.py:combine_transcription_diarization):

        {
          "text": str,                 # full transcript (-> transcript.text)
          "language": str,             # optional; logged only
          "segments": [
            {"start": float, "end": float, "text": str,
             "words": [{"start": float, "end": float, "word": str}]}  # words optional, per segment
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
