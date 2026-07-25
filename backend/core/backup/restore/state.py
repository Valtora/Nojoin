"""Mutable state threaded through the restore stages."""

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Set, Tuple

from backend.core.backup.restore import jobs

logger = logging.getLogger(__name__)


@dataclass
class _RestoreState:
    """Mutable state threaded through the restore stages.

    Collecting the additive-restore bookkeeping here lets ``_restore_backup_sync``
    delegate to cohesive validation/preflight/extraction/finalization stages while
    preserving the exact cross-stage invariants (id remapping, identity matching,
    deferred speaker merges, proxy regeneration) the monolithic implementation relied on.
    """

    job_id: str
    clear_existing: bool
    overwrite_existing: bool
    recordings_dir: Any
    config_path: Any
    user_data_dir: Any
    documents_dir: Any
    # table_name -> { old_id: new_id } for additive foreign-key remapping.
    id_map: Dict[str, Dict[int, int]]
    # Identity key (meeting_uid:/public_id:/audio_path:) -> new recording id, so later
    # backup rows sharing any identifier collapse onto the same restored recording.
    restored_recording_keys: Dict[str, int] = field(default_factory=dict)
    # Identity key -> existing recording row already present in the target database.
    existing_recordings_by_identity: Dict[str, Any] = field(default_factory=dict)
    # Whether the target's existing recordings have been indexed yet. Loaded once, on
    # the first recording row, rather than per row.
    existing_recordings_loaded: bool = False
    # old_id set for recordings skipped under the safe-merge strategy; their children skip too.
    skipped_recording_ids: Set[int] = field(default_factory=set)
    # Deferred self-referential remaps for recording-speaker merges (new_id, old_target_id).
    pending_recording_speaker_merges: List[Tuple[int, int]] = field(
        default_factory=list
    )
    # Restored recordings whose audio landed on disk need a regenerated playback proxy.
    recordings_requiring_proxy: Set[int] = field(default_factory=set)
    # Every recording newly inserted by this restore, for post-restore finalisation.
    restored_recording_ids: Set[int] = field(default_factory=set)
    # Directory the archive's payload is unpacked into. Files only move from here into
    # their real homes once the database transaction has committed, so a failed restore
    # cannot have touched a single existing file.
    staging_dir: Any = None
    # (staging path, final path) pairs applied after the commit succeeds.
    pending_moves: List[Tuple[str, str]] = field(default_factory=list)
    # Final paths already claimed by a pending move, so two restored rows cannot both
    # try to land on the same file.
    claimed_destinations: Set[str] = field(default_factory=set)
    # Progress sink, so a Celery-hosted restore can stream status to its result backend.
    progress_callback: Any = None
    # table_name -> reason -> count, surfaced to the operator when the restore finishes.
    skipped: Dict[str, Dict[str, int]] = field(default_factory=dict)

    def report(self, progress: str) -> None:
        """Publish a progress string to the job record and any external sink."""
        job = jobs.restore_jobs.get(self.job_id)
        if job is not None:
            job["progress"] = progress
        if self.progress_callback is not None:
            try:
                self.progress_callback(progress)
            except Exception:  # noqa: BLE001 -- progress reporting must never fail a restore
                logger.debug("Restore progress callback failed", exc_info=True)

    def stage_path(self, member: str) -> str:
        """Absolute path an archive member was unpacked to."""
        return os.path.abspath(os.path.join(os.fspath(self.staging_dir), member))

    def claim_move(self, staged: str, destination: str) -> bool:
        """Queue a staged file to be moved into place after the commit.

        Returns False when the destination is already spoken for, letting the caller
        pick another name rather than have two rows overwrite one file.
        """
        destination = os.path.abspath(destination)
        if destination in self.claimed_destinations:
            return False
        self.claimed_destinations.add(destination)
        self.pending_moves.append((staged, destination))
        return True

    def record_skip(self, table_name: str, reason: str) -> None:
        """Count one row the restore could not bring across, by table and reason."""
        self.skipped.setdefault(table_name, {})
        self.skipped[table_name][reason] = self.skipped[table_name].get(reason, 0) + 1

    def skip_summary(self) -> Dict[str, Dict[str, int]]:
        """The skip tally, with empty tables omitted so a clean restore reports ``{}``."""
        return {
            table: dict(reasons) for table, reasons in self.skipped.items() if reasons
        }
