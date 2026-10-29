"""Nemotron's stable control namespace, shared with pinned asset preparation."""

from backend.utils.nemotron_manifest import manifest_sha256
from backend.utils.pinned_model_control import (
    PENDING_TTL as PENDING_TTL,
)
from backend.utils.pinned_model_control import (
    PinnedModelControl,
)
from backend.utils.pinned_model_control import (
    atomic_json as atomic_json,
)

control = PinnedModelControl("nemotron", lambda: manifest_sha256())
_control_path = control.path
model_lock = control.model_lock
pending_task = control.pending_task
write_pending = control.write_pending
clear_pending = control.clear_pending
complete_pending = control.complete_pending
