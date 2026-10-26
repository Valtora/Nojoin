"""Native Qwen extraction reaches actual model logits for arbitrary clip lengths."""

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from backend.processing import forced_aligner_native as native


@pytest.fixture
def native_processor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    from tokenizers import Tokenizer
    from tokenizers.models import WordLevel
    from transformers import (
        PreTrainedTokenizerFast,
        Qwen3ASRProcessor,
        WhisperFeatureExtractor,
    )

    tokenizer = PreTrainedTokenizerFast(
        tokenizer_object=Tokenizer(WordLevel({"<unk>": 0}, unk_token="<unk>"))
    )
    tokenizer.audio_token = "<unk>"
    tokenizer.audio_bos_token = "<unk>"
    tokenizer.audio_eos_token = "<unk>"
    processor = Qwen3ASRProcessor(
        WhisperFeatureExtractor(feature_size=128, return_attention_mask=True), tokenizer
    )
    config = processor.feature_extractor.to_dict()
    original = json.dumps(config)
    path = tmp_path / "preprocessor_config.json"
    path.write_text(original)
    monkeypatch.setattr(
        Qwen3ASRProcessor, "from_pretrained", lambda *_args, **_kwargs: processor
    )
    mapped = native.load_native_processor(tmp_path)
    assert path.read_text() == original
    assert type(mapped.feature_extractor).__name__ == "Qwen3ASRFeatureExtractor"
    return mapped


@pytest.mark.parametrize("duration", [29.6, 15.2, 1.7, 60.0])
def test_arbitrary_audio_lengths_reach_native_logits(
    native_processor: Any, duration: float
) -> None:
    import torch
    from transformers import Qwen3ASRConfig, Qwen3ASRForTokenClassification

    features = native_processor.feature_extractor(
        np.zeros(round(duration * 16000), dtype=np.float32),
        sampling_rate=16000,
        padding="longest",
        truncation=False,
        return_tensors="pt",
        return_attention_mask=True,
    )
    assert features.input_features.shape[-1] % 100 == 0
    assert int(features.attention_mask.sum()) == round(duration * 100)
    config = Qwen3ASRConfig(
        audio_config={
            "model_type": "qwen3_asr_encoder",
            "encoder_layers": 1,
            "encoder_attention_heads": 2,
            "encoder_ffn_dim": 16,
            "d_model": 8,
            "output_dim": 8,
            "downsample_hidden_size": 2,
        },
        text_config={
            "model_type": "qwen3",
            "vocab_size": 8,
            "hidden_size": 8,
            "intermediate_size": 16,
            "num_hidden_layers": 1,
            "num_attention_heads": 2,
            "num_key_value_heads": 1,
            "head_dim": 4,
        },
        audio_token_id=2,
        timestamp_token_id=3,
        pad_token_id=0,
        eos_token_id=1,
        num_labels=4,
    )
    model = Qwen3ASRForTokenClassification(config).eval()
    n_audio = int(
        native_processor._get_audio_token_length(features.attention_mask.sum(-1))[0]
    )
    ids = torch.tensor([[2] * n_audio + [3, 3]])
    with torch.inference_mode():
        output = model(
            input_ids=ids,
            input_features=features.input_features,
            input_features_mask=features.attention_mask,
            use_cache=False,
        )
    assert output.logits.shape == (1, n_audio + 2, 4)
    assert bool(torch.isfinite(output.logits).all())
