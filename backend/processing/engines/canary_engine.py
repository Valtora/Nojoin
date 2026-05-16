# nojoin/processing/engines/canary_engine.py
# Canary transcription engine: a thin subclass of the shared OnnxAsrEngine.
# The onnx-asr logic and the timestamped-result mapper live in onnx_asr_engine.

from .onnx_asr_engine import OnnxAsrEngine


class CanaryEngine(OnnxAsrEngine):
    """Canary 1B transcription engine backed by onnx-asr."""

    name = "canary"
    config_key = "canary_model"
    default_model_id = "nemo-canary-1b-v2"
    # onnx_id_map empty: the Nojoin model id is already the onnx-asr name.
