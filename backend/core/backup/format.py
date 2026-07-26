"""Archive format constants and the restore's foreign-key classification.

Pure data. Nothing here reads the database or the filesystem, which is what lets the
parity test and the restore agree on one description of the contract.
"""

import os
from dataclasses import dataclass
from typing import Dict, Tuple

from backend.models.calendar import CalendarProvider

BACKUP_FORMAT_VERSION = 2


LEGACY_BACKUP_FORMAT_VERSION = 1


ARCHIVE_QUALITY_COMPRESSED = "compressed"


ARCHIVE_QUALITY_ORIGINAL = "original"


ARCHIVE_QUALITIES = (ARCHIVE_QUALITY_COMPRESSED, ARCHIVE_QUALITY_ORIGINAL)


ARCHIVABLE_AUDIO_EXTENSIONS = frozenset(
    {".wav", ".mp3", ".m4a", ".ogg", ".flac", ".opus"}
)


RESTORE_STAGING_DIRNAME = "restore_staging"


BACKUP_EXPORT_DIR = os.getenv("BACKUP_EXPORT_DIR", "/tmp/nojoin_backups")


RESTORE_LOCK_KEY = "nojoin:backup:restore-lock"


RESTORE_LOCK_TTL_SECONDS = 6 * 60 * 60


RESTORE_DISK_HEADROOM_BYTES = 512 * 1024 * 1024


MICROSOFT_COMMON_TENANT = "common"


CALENDAR_PROVIDER_ENV_KEYS: Dict[str, Dict[str, str | None]] = {
    CalendarProvider.GOOGLE.value: {
        "client_id": "GOOGLE_OAUTH_CLIENT_ID",
        "client_secret": "GOOGLE_OAUTH_CLIENT_SECRET",
        "tenant_id": None,
    },
    CalendarProvider.MICROSOFT.value: {
        "client_id": "MICROSOFT_OAUTH_CLIENT_ID",
        "client_secret": "MICROSOFT_OAUTH_CLIENT_SECRET",
        "tenant_id": "MICROSOFT_OAUTH_TENANT_ID",
    },
}


@dataclass(frozen=True)
class _ForeignKeySpec:
    """One restorable foreign-key column and how the restore should treat it."""

    column: str
    # Key into ``id_map``. A table absent from ``MODELS`` (e.g. "invitations") never
    # populates its map, so every reference to it resolves as unmappable.
    target_table: str
    # Ownership links decide whether the row means anything at all: if the target
    # cannot be resolved the row is skipped. Nullability is deliberately NOT the test
    # here. Several ownership columns (Recording.user_id, Tag.user_id, PeopleTag.user_id,
    # GlobalSpeaker.user_id) are nullable in the database, yet every read path is scoped
    # by user_id, so a null owner produces a row that no user can ever see.
    #
    # Enrichment links only decorate the row: if the target cannot be resolved the
    # column is nulled and the row is still restored, because losing an optional link is
    # always better than losing the data the row carries.
    ownership: bool


def _own(column: str, target_table: str) -> _ForeignKeySpec:
    return _ForeignKeySpec(column=column, target_table=target_table, ownership=True)


def _enrich(column: str, target_table: str) -> _ForeignKeySpec:
    return _ForeignKeySpec(column=column, target_table=target_table, ownership=False)


RESTORE_FOREIGN_KEYS: Dict[str, Tuple[_ForeignKeySpec, ...]] = {
    "users": (_enrich("invitation_id", "invitations"),),
    "calendar_provider_configs": (),
    "calendar_connections": (_own("user_id", "users"),),
    "calendar_sources": (_own("connection_id", "calendar_connections"),),
    "calendar_events": (_own("calendar_id", "calendar_sources"),),
    "user_tasks": (_own("user_id", "users"),),
    "p_tags": (_own("user_id", "users"), _enrich("parent_id", "p_tags")),
    "global_speakers": (_own("user_id", "users"),),
    "people_tag_links": (
        _own("global_speaker_id", "global_speakers"),
        _own("tag_id", "p_tags"),
    ),
    "tags": (_own("user_id", "users"), _enrich("parent_id", "tags")),
    "user_task_tags": (_own("task_id", "user_tasks"), _own("tag_id", "tags")),
    "recordings": (
        _own("user_id", "users"),
        _enrich("calendar_event_id", "calendar_events"),
    ),
    "user_task_recordings": (
        _own("task_id", "user_tasks"),
        _own("recording_id", "recordings"),
    ),
    "recording_speakers": (
        _own("recording_id", "recordings"),
        _enrich("global_speaker_id", "global_speakers"),
        # Back-references into canonical pipeline tables that are rebuilt rather than
        # archived. Their targets never exist on the restore side, so they are nulled;
        # carrying the source system's ids would fail the constraint and take the whole
        # speaker row, and therefore the speaker's name, down with it.
        _enrich("processing_run_id", "processing_runs"),
        _enrich("last_speaker_correction_event_id", "speaker_correction_events"),
        _enrich("last_diarization_window_result_id", "diarization_window_results"),
    ),
    "recording_tags": (_own("recording_id", "recordings"), _own("tag_id", "tags")),
    # user_id is enrichment rather than ownership despite being the owning column:
    # install-tier templates are owned by the installation and carry a NULL user_id
    # by design, and an ownership link skips every row whose owner is NULL.
    "notes_templates": (_enrich("user_id", "users"),),
    "transcripts": (
        _own("recording_id", "recordings"),
        # Provenance only. transcripts.notes_template_sections holds the structure
        # text itself, so losing the link costs a label, never the notes.
        _enrich("notes_template_id", "notes_templates"),
    ),
    "chat_messages": (_own("recording_id", "recordings"), _own("user_id", "users")),
    "documents": (_own("recording_id", "recordings"),),
}


UNARCHIVED_TABLES: Dict[str, str] = {
    "async_task_ownerships": "Ephemeral ownership records for in-flight Celery tasks.",
    "calendar_push_channels": (
        "Provider webhook subscriptions bound to this installation's public URL; they "
        "would point at the wrong host after a restore."
    ),
    "cli_oauth_credentials": "Machine-bound CLI OAuth credentials; re-authenticate on the target.",
    "cli_usage_daily": "Per-installation CLI usage counters.",
    "context_chunks": (
        "RAG embeddings, regenerated from the restored transcript and documents by "
        "finalize_restored_recording_task."
    ),
    "invitations": (
        "Not archived by design; User.invitation_id is provenance only and is nulled on "
        "restore. See RESTORE_FOREIGN_KEYS."
    ),
    "oauth_clients": "This installation's OAuth server registrations.",
    "oauth_authorization_codes": "Short-lived OAuth codes.",
    "oauth_refresh_tokens": "Tokens issued against this installation's signing key.",
    "revoked_jwts": "Revocation list for this installation's signing key.",
    # Canonical pipeline state, rebuilt from the transcript projection on restore.
    # transcript.segments carries the manual edit flags, so hand corrections survive the
    # round trip; what is lost is audit history. See docs/adr/0003.
    "processing_runs": "Canonical pipeline state; rebuilt from the transcript projection.",
    "recording_asr_window_results": "Canonical pipeline state; rebuilt.",
    "recording_audio_chunks": "Live capture scratch data for in-flight recordings.",
    "recording_audio_window_manifests": "Live capture scratch data for in-flight recordings.",
    "recording_speaker_aliases": "Canonical pipeline state; rebuilt.",
    "speaker_correction_events": "Canonical pipeline audit history; not rebuilt.",
    "diarization_window_results": "Canonical pipeline state; rebuilt.",
    "diarization_window_turns": "Canonical pipeline state; rebuilt.",
    "transcript_utterances": "Canonical pipeline state; rebuilt from transcript.segments.",
    "transcript_utterance_events": "Canonical pipeline audit history; not rebuilt.",
}


DEFERRED_FOREIGN_KEYS: Dict[str, Tuple[str, ...]] = {
    "recording_speakers": ("merged_into_id",),
}


TRANSIENT_RECORDING_STATUSES = frozenset(
    {"UPLOADING", "QUEUED", "PROCESSING", "PAUSED"}
)


RESTORED_RECORDING_TERMINAL_STATUS = "PROCESSED"


RESTORED_RECORDING_INTERRUPTED_STATUS = "ERROR"


RESTORED_RECORDING_INTERRUPTED_MESSAGE = (
    "This recording was still being processed when the backup was taken, so it was "
    "restored without a completed transcript. Reprocess it to finish."
)


SKIP_REASON_UNRESOLVED_OWNER = "unresolved_owner"


SKIP_REASON_NO_IDENTITY = "no_identity"


SKIP_REASON_INSERT_FAILED = "insert_failed"
