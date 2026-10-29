"""Reviewed in-memory adaptations for the immutable native Qwen aligner."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from backend.utils.forced_aligner_manifest import ForcedAlignerError

TRANSFORMERS_VERSION = "5.20.2"
KEY_MAPPING = {
    r"^thinker\.audio_tower\.proj1\.": "model.multi_modal_projector.linear_1.",
    r"^thinker\.audio_tower\.proj2\.": "model.multi_modal_projector.linear_2.",
    r"^thinker\.audio_tower\.": "model.audio_tower.",
    r"^thinker\.model\.": "model.language_model.",
    r"^thinker\.lm_head\.": "score.",
}
ALIGNMENT_TEMPLATE = """{%- set ns = namespace(audio_tokens='', words=[]) -%}
{%- for m in messages -%}{%- if m.content is not string -%}
{%- for c in m.content -%}
{%- if c.type == 'audio' or ('audio' in c) or ('audio_url' in c) -%}
{%- set ns.audio_tokens = ns.audio_tokens + '<|audio_start|><|audio_pad|><|audio_end|>' -%}
{%- endif -%}
{%- if c.type == 'text' and (c.text is defined) -%}
{%- set ns.words = ns.words + [c.text] -%}{%- endif -%}
{%- endfor -%}{%- endif -%}{%- endfor -%}
{{- ns.audio_tokens + ns.words | join('<timestamp><timestamp>') + '<timestamp><timestamp>' -}}"""


def native_config(path: Path, config_class: Any) -> Any:
    """Promote pinned nested dimensions without changing checkpoint files."""
    raw = json.loads((path / "config.json").read_text(encoding="utf-8"))
    thinker = raw["thinker_config"]
    audio = dict(thinker["audio_config"], model_type="qwen3_asr_encoder")
    return config_class(
        audio_config=audio,
        text_config=thinker["text_config"],
        num_labels=thinker["classify_num"],
        audio_token_id=thinker["audio_token_id"],
        timestamp_token_id=raw["timestamp_token_id"],
        timestamp_segment_time=raw["timestamp_segment_time"],
        token_classification_bias=False,
    )


def mapped_key(source: str) -> str:
    """Map every checkpoint key exactly once using ordered prefix rules."""
    for pattern, replacement in KEY_MAPPING.items():
        if re.search(pattern, source):
            return re.sub(pattern, replacement, source)
    raise ForcedAlignerError("Unmapped forced-aligner checkpoint key")


def verify_weight_mapping(path: Path, config: Any, model_class: Any) -> None:
    """Reject missing, extra, colliding or differently shaped checkpoint tensors."""
    import torch
    from safetensors import safe_open

    supplied = {}
    with safe_open(
        str(path / "model.safetensors"), framework="pt", device="cpu"
    ) as checkpoint:
        for source in checkpoint.keys():
            target = mapped_key(source)
            if target in supplied:
                raise ForcedAlignerError("Colliding forced-aligner checkpoint mapping")
            supplied[target] = list(checkpoint.get_slice(source).get_shape())
    with torch.device("meta"):
        expected_model = model_class(config)
    expected = {
        key: list(value.shape) for key, value in expected_model.state_dict().items()
    }
    if supplied != expected:
        raise ForcedAlignerError(
            "Forced-aligner checkpoint keys or tensor shapes differ"
        )


def load_native_model(path: Path) -> Any:
    """Load explicit native safetensors only after complete mapping validation."""
    import torch
    import transformers
    from transformers import Qwen3ASRConfig, Qwen3ASRForTokenClassification

    require_transformers_version(transformers.__version__)
    config = native_config(path, Qwen3ASRConfig)
    verify_weight_mapping(path, config, Qwen3ASRForTokenClassification)
    dtype = torch.float32
    model, diagnostics = Qwen3ASRForTokenClassification.from_pretrained(
        str(path),
        config=config,
        key_mapping=KEY_MAPPING,
        local_files_only=True,
        trust_remote_code=False,
        use_safetensors=True,
        dtype=dtype,
        output_loading_info=True,
        attn_implementation="sdpa",
    )
    if any(diagnostics.values()) or type(model) is not Qwen3ASRForTokenClassification:
        raise ForcedAlignerError(
            "Native forced-aligner loading diagnostics are not clean"
        )
    if any(parameter.dtype != dtype for parameter in model.parameters()):
        raise ForcedAlignerError("Native forced-aligner parameter precision differs")
    return model.eval().to("cpu")


def require_transformers_version(version: str) -> None:
    """Refuse silent drift from the evaluated native implementation."""
    if version != TRANSFORMERS_VERSION:
        raise ForcedAlignerError("Forced alignment requires Transformers 5.20.2")


def load_native_processor(path: Path) -> Any:
    """Read the verified local processor with the evaluated native class."""
    import transformers
    from transformers import Qwen3ASRFeatureExtractor, Qwen3ASRProcessor

    require_transformers_version(transformers.__version__)
    processor = Qwen3ASRProcessor.from_pretrained(
        str(path), local_files_only=True, trust_remote_code=False
    )
    if type(processor) is not Qwen3ASRProcessor:
        raise ForcedAlignerError(
            "Forced alignment loaded an unexpected processor class"
        )
    # The pinned checkpoint names Whisper's extractor. Promote its configuration
    # in memory, as for the model above: the native extractor owns chunk padding
    # (Transformers 5.20.2, feature_extraction_qwen3_asr.py:219–230).
    feature_config = json.loads(
        (path / "preprocessor_config.json").read_text(encoding="utf-8")
    )
    feature_config["feature_extractor_type"] = "Qwen3ASRFeatureExtractor"
    processor.feature_extractor = Qwen3ASRFeatureExtractor.from_dict(feature_config)
    return processor
