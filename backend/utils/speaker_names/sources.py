"""Who may name a speaker, and how much each source counts.

The order is a self-introduction the transcript shows ("I'm Alex") below the
LLM, below a voiceprint match, below the user. A name the confirm-at-end
setting promoted (``auto``) sits just under the user: nobody looked at it, but
the user asked for names to be taken as given.
"""

from __future__ import annotations

from typing import Any

SOURCE_HEURISTIC = "heuristic"
SOURCE_LLM = "llm"
SOURCE_VOICEPRINT = "voiceprint"
SOURCE_AUTO = "auto"
SOURCE_USER = "user"

AUTHORITY: dict[str, int] = {
    SOURCE_HEURISTIC: 10,
    SOURCE_LLM: 20,
    SOURCE_VOICEPRINT: 30,
    SOURCE_AUTO: 35,
    SOURCE_USER: 40,
}

KIND_NAME = "name"
KIND_DISMISS = "dismiss"

# Sources whose events the final pass derives again on every run.
MACHINE_SOURCES = frozenset(
    {SOURCE_HEURISTIC, SOURCE_LLM, SOURCE_VOICEPRINT, SOURCE_AUTO}
)


def suggestion_source(suggestion: dict[str, Any]) -> str:
    """The source of a persisted name suggestion: the self-introduction rule
    or the LLM."""
    if suggestion.get("source") == "deterministic_rule":
        return SOURCE_HEURISTIC
    return SOURCE_LLM


def inferred_name_may_be_replaced(speaker: Any) -> bool:
    """A pending self-introduction or LLM name, which a new inference may replace.

    The LLM ranks at or above both. A voiceprint match ranks above it and a
    user's name above everything, so neither is offered to it.
    """
    return name_is_pending(speaker) and getattr(speaker, "name_source", None) in (
        SOURCE_HEURISTIC,
        SOURCE_LLM,
    )


def name_is_pending(speaker: Any) -> bool:
    """True for a name nobody has confirmed: shown with "?" and a Confirm.

    A name with a source and no lock is pending. NULL is a name from before
    sources were recorded, and ``auto`` one the confirm-at-end setting took as
    given; both show as they always did.
    """
    source = getattr(speaker, "name_source", None)
    if source is None or source == SOURCE_AUTO:
        return False
    return not bool(getattr(speaker, "identity_locked", False))
