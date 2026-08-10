"""Shared read-modify-write for the transcript's analytics payload.

Two tiers store into one JSONB column: measured delivery on the CPU lane, and
the AI analysis on the IO lane. They are dispatched independently, so a task
that assigned the whole column would drop whatever the other had just written.
Both go through here instead, and both take a row lock first, so the last
writer wins the key it owns rather than the column.
"""

from __future__ import annotations

from typing import Any, Mapping

# Keys owned by each tier inside ``transcripts.analytics_payload``. The
# delivery tier also carries its own metadata at the top level, which predates
# the AI tier and is left there so existing payloads keep reading.
DELIVERY_KEY = "delivery"
AI_KEY = "ai"


def merge_analytics_payload(
    existing: Mapping[str, Any] | None,
    updates: Mapping[str, Any],
) -> dict[str, Any]:
    """Return a new payload with ``updates`` applied over ``existing``.

    A shallow merge on purpose: each tier owns whole top-level keys, so a deep
    merge would let a stale sub-key of one run survive into the next.
    """
    merged = dict(existing or {})
    merged.update(updates)
    return merged
