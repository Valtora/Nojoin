# Parakeet transcription engine: a thin subclass of the shared OnnxAsrEngine.
# The onnx-asr logic and the timestamped-result mapper live in onnx_asr_engine.

from ...utils.transcription_models import PARAKEET_MODEL_IDS
from .onnx_asr_engine import OnnxAsrEngine
from .onnx_asr_engine import (
    map_onnx_asr_result as map_parakeet_result,  # noqa: F401  backward-compat alias
)


class ParakeetEngine(OnnxAsrEngine):
    """Parakeet transcription engine backed by onnx-asr."""

    name = "parakeet"
    config_key = "parakeet_model"
    default_model_id = "parakeet-tdt-0.6b-v3"
    onnx_id_map = PARAKEET_MODEL_IDS
    recovers_skipped_speech = True
