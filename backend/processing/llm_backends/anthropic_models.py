"""What a Claude model accepts, decided from its model id.

The Anthropic API has no capability flag for the two request parameters Nojoin
cares about here, and the SDK's ``models.retrieve`` is a network call the
worker should not make on every request. The rules below are the published
per-model ones, keyed on the model line and version in the id, so a request
never carries a parameter the model would reject with a 400.

Unknown ids resolve to "accepts nothing", which is exactly what Nojoin sent
before these rules existed. A new model line therefore works on the day it
ships, at the provider's defaults, and gains the tuned parameters when this
table learns about it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# ``claude-opus-4-6``, ``claude-haiku-4-5-20251001``, ``claude-sonnet-5``,
# ``claude-fable-5-1``: the line first, then major[-minor], then an optional
# release date.
_LINE_FIRST = re.compile(
    r"^claude-(?P<line>opus|sonnet|haiku|fable|mythos)-(?P<major>\d+)"
    r"(?:-(?P<minor>\d+))?(?:-\d{8})?$"
)
# ``claude-3-5-haiku-20241022``, ``claude-3-haiku-20240307``: the Claude 3
# generation put the version first.
_VERSION_FIRST = re.compile(
    r"^claude-(?P<major>\d+)(?:-(?P<minor>\d+))?-(?P<line>opus|sonnet|haiku)"
    r"(?:-\d{8})?$"
)


@dataclass(frozen=True)
class AnthropicModelCapabilities:
    """The request parameters a model accepts, and how it behaves without them."""

    # ``output_config.effort`` is accepted. It is a 400 on Sonnet 4.5, Haiku 4.5
    # and everything older.
    accepts_effort: bool
    # Omitting ``thinking`` still runs adaptive thinking, so thinking tokens count
    # against ``max_tokens`` on every call that does not turn it off.
    thinks_by_default: bool
    # A non-default ``temperature`` is accepted. Opus 4.7 and later reject the
    # parameter outright; Sonnet 5, Sonnet 5.5 and Haiku 5.5 reject any value but
    # the default; Fable has no sampling control. The SDK does not type the
    # parameter, so it travels in ``extra_body`` where it is accepted.
    accepts_temperature: bool = False


_UNKNOWN = AnthropicModelCapabilities(accepts_effort=False, thinks_by_default=False)


def _parse(model: str) -> tuple[str, tuple[int, int]] | None:
    name = (model or "").strip().lower()
    if name == "claude-mythos-preview":
        return "mythos", (5, 0)
    match = _LINE_FIRST.match(name) or _VERSION_FIRST.match(name)
    if not match:
        return None
    minor = match.group("minor")
    return match.group("line"), (int(match.group("major")), int(minor or 0))


def anthropic_model_capabilities(model: str) -> AnthropicModelCapabilities:
    """Capabilities for ``model``; the conservative default for an id not understood."""
    parsed = _parse(model)
    if parsed is None:
        return _UNKNOWN
    line, version = parsed
    if line in ("fable", "mythos"):
        return AnthropicModelCapabilities(accepts_effort=True, thinks_by_default=True)
    if line == "opus":
        return AnthropicModelCapabilities(
            accepts_effort=version >= (4, 5),
            thinks_by_default=version >= (5, 0),
            accepts_temperature=version <= (4, 6),
        )
    if line == "sonnet":
        return AnthropicModelCapabilities(
            accepts_effort=version >= (4, 6),
            thinks_by_default=version >= (5, 0),
            accepts_temperature=version <= (4, 6),
        )
    # haiku
    return AnthropicModelCapabilities(
        accepts_effort=version >= (5, 5),
        thinks_by_default=version >= (5, 5),
        accepts_temperature=version <= (4, 5),
    )
