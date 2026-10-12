"""The Anthropic model-capability table, keyed on the model id.

Each row is a published per-model rule. A wrong answer here is a 400 on every
request to that model, so the table is pinned by test rather than trusted.
"""

from __future__ import annotations

import pytest

from backend.processing.llm_backends.anthropic_models import (
    anthropic_model_capabilities,
)


@pytest.mark.parametrize(
    ("model", "accepts_effort", "thinks_by_default"),
    [
        ("claude-fable-5-1", True, True),
        ("claude-mythos-preview", True, True),
        ("claude-opus-5-5", True, True),
        ("claude-opus-5", True, True),
        # Opus 4.5 to 4.8 accept effort but run without thinking unless asked.
        ("claude-opus-4-8", True, False),
        ("claude-opus-4-6", True, False),
        ("claude-opus-4-5", True, False),
        ("claude-opus-4-1-20250805", False, False),
        ("claude-sonnet-5-5", True, True),
        ("claude-sonnet-5", True, True),
        ("claude-sonnet-4-6", True, False),
        # Sonnet 4.5 and Haiku 4.5 reject output_config.effort outright.
        ("claude-sonnet-4-5", False, False),
        ("claude-sonnet-4-0", False, False),
        ("claude-haiku-5-5", True, True),
        ("claude-haiku-4-5", False, False),
        ("claude-haiku-4-5-20251001", False, False),
        ("claude-3-5-haiku-20241022", False, False),
        ("claude-3-haiku-20240307", False, False),
    ],
)
def test_capabilities_follow_the_published_per_model_rules(
    model: str, accepts_effort: bool, thinks_by_default: bool
) -> None:
    capabilities = anthropic_model_capabilities(model)
    assert capabilities.accepts_effort is accepts_effort
    assert capabilities.thinks_by_default is thinks_by_default


@pytest.mark.parametrize("model", ["", "claude-test", "gpt-5", "claude-opus"])
def test_an_unrecognised_id_accepts_nothing(model: str) -> None:
    # The conservative answer is what Nojoin sent before the table existed, so a
    # new model line works at the provider's defaults until the table learns it.
    capabilities = anthropic_model_capabilities(model)
    assert capabilities.accepts_effort is False
    assert capabilities.thinks_by_default is False
    assert capabilities.accepts_temperature is False


@pytest.mark.parametrize(
    ("model", "accepts_temperature"),
    [
        # Opus 4.7 and later reject the parameter outright.
        ("claude-opus-5-5", False),
        ("claude-opus-4-8", False),
        ("claude-opus-4-7", False),
        ("claude-opus-4-6", True),
        ("claude-opus-4-5", True),
        ("claude-opus-4-1-20250805", True),
        # Sonnet 5 and 5.5 and Haiku 5.5 reject any value but the default.
        ("claude-sonnet-5-5", False),
        ("claude-sonnet-5", False),
        ("claude-sonnet-4-6", True),
        ("claude-sonnet-4-5", True),
        ("claude-haiku-5-5", False),
        ("claude-haiku-4-5", True),
        ("claude-3-5-haiku-20241022", True),
        # Fable has no sampling control.
        ("claude-fable-5-1", False),
        ("claude-mythos-preview", False),
        ("claude-test", False),
    ],
)
def test_temperature_goes_only_to_models_that_still_take_it(
    model: str, accepts_temperature: bool
) -> None:
    assert (
        anthropic_model_capabilities(model).accepts_temperature is accepts_temperature
    )


def test_ids_are_matched_case_insensitively_and_trimmed() -> None:
    assert anthropic_model_capabilities(" Claude-Opus-5-5 ").accepts_effort is True
