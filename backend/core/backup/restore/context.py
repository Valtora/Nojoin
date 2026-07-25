"""The per-row unit the restore's table resolvers operate on."""

from dataclasses import dataclass
from enum import Enum, auto
from typing import Any, Dict

from backend.core.backup.restore.state import _RestoreState


class RowOutcome(Enum):
    """What the runner should do with a row after a resolver has seen it."""

    #: Nothing already covers this row; insert it.
    INSERT = auto()
    #: The resolver reconciled the row itself, by mapping it onto an existing record or
    #: deciding it must be dropped. The runner moves on without inserting.
    HANDLED = auto()


@dataclass
class _RowContext:
    """One archive row, mid-restore.

    Resolvers read and mutate ``item_data`` in place and may record outputs the runner
    needs after the insert, which is why this is a mutable object rather than arguments.
    """

    session: Any
    state: _RestoreState
    table_name: str
    model_cls: Any
    item_data: Dict[str, Any]
    old_id: Any
    #: Staged file backing this row, so the runner can size it and flag a proxy rebuild.
    pending_audio_source: str | None = None
    #: Self-referential speaker merge target, reattached in a second pass.
    old_recording_speaker_merge_id: Any = None
