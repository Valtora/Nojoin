# nojoin/processing/engines/parakeet_engine.py
# Parakeet transcription engine: a thin subclass of the shared OnnxAsrEngine.
# The onnx-asr logic and the timestamped-result mapper live in onnx_asr_engine.

from .onnx_asr_engine import OnnxAsrEngine
from .onnx_asr_engine import map_onnx_asr_result as map_parakeet_result  # noqa: F401  backward-compat alias


class ParakeetEngine(OnnxAsrEngine):
    """Parakeet transcription engine backed by onnx-asr."""

    name = "parakeet"
    config_key = "parakeet_model"
    default_model_id = "parakeet-tdt-0.6b-v3"
    onnx_id_map = {"parakeet-tdt-0.6b-v3": "nemo-parakeet-tdt-0.6b-v3"}
