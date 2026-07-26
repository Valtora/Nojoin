"""Shared SQLite CREATE TABLE statements for endpoint and service tests.

These mirror the SQLModel table definitions closely enough for SQLite-backed
tests that exercise real ORM queries. New tests should import from here
instead of redefining per-file copies (older test modules still carry their
own local copies; consolidate opportunistically when touching them).
"""

USERS_SCHEMA = """
CREATE TABLE users (
    id INTEGER PRIMARY KEY,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    username VARCHAR NOT NULL,
    hashed_password VARCHAR NOT NULL,
    is_active BOOLEAN NOT NULL,
    is_superuser BOOLEAN NOT NULL,
    force_password_change BOOLEAN NOT NULL,
    role VARCHAR NOT NULL,
    token_version INTEGER NOT NULL,
    settings JSON,
    has_seen_demo_recording BOOLEAN NOT NULL,
    has_seen_companion_retirement_notice BOOLEAN NOT NULL DEFAULT 0,
    invitation_id INTEGER
)
"""

NOTES_TEMPLATES_SCHEMA = """
CREATE TABLE notes_templates (
    id INTEGER PRIMARY KEY,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    name VARCHAR NOT NULL,
    description TEXT,
    sections TEXT NOT NULL,
    scope VARCHAR NOT NULL DEFAULT 'personal',
    user_id INTEGER,
    builtin_version INTEGER
)
"""

USER_TASKS_SCHEMA = """
CREATE TABLE user_tasks (
    id INTEGER PRIMARY KEY,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    title VARCHAR(255) NOT NULL,
    body TEXT,
    due_at DATETIME,
    completed_at DATETIME,
    archived_at DATETIME,
    user_id INTEGER NOT NULL
)
"""

RECORDINGS_SCHEMA = """
CREATE TABLE recordings (
    max_speakers INTEGER,
    id INTEGER PRIMARY KEY,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    name VARCHAR(255) NOT NULL,
    public_id VARCHAR(36) NOT NULL,
    meeting_uid VARCHAR(36) NOT NULL,
    audio_path VARCHAR(1024) NOT NULL,
    proxy_path VARCHAR(1024),
    celery_task_id VARCHAR(255),
    duration_seconds FLOAT,
    file_size_bytes INTEGER,
    status VARCHAR(32) NOT NULL,
    client_status VARCHAR(32),
    upload_progress INTEGER NOT NULL,
    processing_progress INTEGER NOT NULL,
    processing_step VARCHAR(255),
    processing_started_at DATETIME,
    processing_completed_at DATETIME,
    pipeline_generation VARCHAR(32) DEFAULT 'unified',
    is_archived BOOLEAN NOT NULL,
    is_deleted BOOLEAN NOT NULL,
    last_activity_at DATETIME,
    user_id INTEGER,
    calendar_event_id INTEGER
)
"""

TRANSCRIPTS_SCHEMA = """
CREATE TABLE transcripts (
    id INTEGER PRIMARY KEY,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    recording_id INTEGER NOT NULL UNIQUE,
    text TEXT,
    segments JSON,
    notes TEXT,
    user_notes TEXT,
    meeting_edge_focus TEXT,
    meeting_edge_payload JSON,
    meeting_edge_status VARCHAR(32) NOT NULL DEFAULT 'idle',
    meeting_edge_error_message TEXT,
    meeting_edge_source_signature TEXT,
    speaker_name_suggestions JSON,
    notes_template_id INTEGER,
    notes_template_sections TEXT,
    notes_status VARCHAR(32) NOT NULL,
    transcript_status VARCHAR(32) NOT NULL,
    error_message TEXT
)
"""

GLOBAL_SPEAKERS_SCHEMA = """
CREATE TABLE global_speakers (
    embedding_version INTEGER,
    id INTEGER PRIMARY KEY,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    user_id INTEGER,
    name VARCHAR(255),
    embedding JSON,
    is_voiceprint_locked BOOLEAN NOT NULL DEFAULT 0,
    color VARCHAR(32),
    title VARCHAR(255),
    company VARCHAR(255),
    email VARCHAR(255),
    phone_number VARCHAR(255),
    notes TEXT
)
"""

RECORDING_SPEAKERS_SCHEMA = """
CREATE TABLE recording_speakers (
    embedding_version INTEGER,
    id INTEGER PRIMARY KEY,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    public_id VARCHAR(36) NOT NULL,
    recording_id INTEGER NOT NULL,
    global_speaker_id INTEGER,
    diarization_label VARCHAR(255),
    local_name VARCHAR(255),
    name VARCHAR(255),
    snippet_start FLOAT,
    snippet_end FLOAT,
    voice_snippet_path VARCHAR(1024),
    embedding JSON,
    color VARCHAR(32),
    merged_into_id INTEGER,
    speaker_status VARCHAR(32) NOT NULL,
    speaker_kind VARCHAR(32) NOT NULL,
    processing_run_id INTEGER,
    last_speaker_correction_event_id INTEGER,
    last_diarization_window_result_id INTEGER,
    first_seen_ms INTEGER,
    last_seen_ms INTEGER,
    identity_confidence FLOAT,
    identity_locked BOOLEAN NOT NULL
)
"""

PROCESSING_RUNS_SCHEMA = """
CREATE TABLE processing_runs (
    id INTEGER PRIMARY KEY,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    public_id VARCHAR(36) NOT NULL,
    recording_id INTEGER NOT NULL,
    parent_run_id INTEGER,
    run_kind VARCHAR(32) NOT NULL,
    trigger_source VARCHAR(32) NOT NULL,
    requested_by_user_id INTEGER,
    status VARCHAR(32) NOT NULL,
    config_hash VARCHAR(255),
    transcription_backend VARCHAR(255),
    diarization_backend VARCHAR(255),
    model_metadata JSON,
    span_start_ms INTEGER,
    span_end_ms INTEGER,
    reused_live_asr BOOLEAN NOT NULL,
    idempotency_key VARCHAR(255),
    metrics JSON,
    error_summary TEXT,
    started_at DATETIME,
    completed_at DATETIME
)
"""

DIARIZATION_WINDOW_RESULTS_SCHEMA = """
CREATE TABLE diarization_window_results (
    id INTEGER PRIMARY KEY,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    public_id VARCHAR(36) NOT NULL,
    recording_id INTEGER NOT NULL,
    processing_run_id INTEGER,
    window_index INTEGER NOT NULL,
    window_start_ms INTEGER NOT NULL,
    window_end_ms INTEGER NOT NULL,
    chunk_start_sequence INTEGER,
    chunk_end_sequence INTEGER,
    model_name VARCHAR(255),
    model_version VARCHAR(255),
    device VARCHAR(255),
    config_hash VARCHAR(255),
    status VARCHAR(32) NOT NULL,
    raw_payload JSON
)
"""

DIARIZATION_WINDOW_TURNS_SCHEMA = """
CREATE TABLE diarization_window_turns (
    id INTEGER PRIMARY KEY,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    window_result_id INTEGER NOT NULL,
    local_speaker_key VARCHAR(255) NOT NULL,
    start_ms INTEGER NOT NULL,
    end_ms INTEGER NOT NULL,
    confidence FLOAT,
    matched_recording_speaker_id INTEGER,
    metadata_payload JSON
)
"""

TRANSCRIPT_UTTERANCES_SCHEMA = """
CREATE TABLE transcript_utterances (
    id INTEGER PRIMARY KEY,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    public_id VARCHAR(36) NOT NULL,
    recording_id INTEGER NOT NULL,
    sort_key VARCHAR(64) NOT NULL,
    start_ms INTEGER NOT NULL,
    end_ms INTEGER NOT NULL,
    text TEXT NOT NULL,
    speaker_label VARCHAR(255),
    recording_speaker_id INTEGER,
    state VARCHAR(32) NOT NULL,
    source_kind VARCHAR(255) NOT NULL,
    processing_run_id INTEGER,
    last_utterance_event_id INTEGER,
    last_diarization_window_result_id INTEGER,
    revision INTEGER NOT NULL,
    overlap_group_id VARCHAR(64),
    overlap_rank INTEGER NOT NULL,
    manual_text_locked BOOLEAN NOT NULL,
    manual_speaker_locked BOOLEAN NOT NULL,
    speaker_assignment_source VARCHAR(32) NOT NULL,
    speaker_assignment_authority VARCHAR(32) NOT NULL,
    text_confidence FLOAT,
    speaker_confidence FLOAT,
    confidence_payload JSON
)
"""

TRANSCRIPT_UTTERANCE_EVENTS_SCHEMA = """
CREATE TABLE transcript_utterance_events (
    id INTEGER PRIMARY KEY,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    recording_id INTEGER NOT NULL,
    utterance_id INTEGER NOT NULL,
    processing_run_id INTEGER,
    actor_user_id INTEGER,
    event_type VARCHAR(64) NOT NULL,
    source VARCHAR(64) NOT NULL,
    old_values JSON,
    new_values JSON,
    resulting_revision INTEGER NOT NULL
)
"""

RECORDING_SPEAKER_ALIASES_SCHEMA = """
CREATE TABLE recording_speaker_aliases (
    id INTEGER PRIMARY KEY,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    recording_speaker_id INTEGER NOT NULL,
    alias_type VARCHAR(64) NOT NULL,
    alias_value VARCHAR(255) NOT NULL,
    source_run_id INTEGER,
    active BOOLEAN NOT NULL,
    valid_from_ms INTEGER,
    valid_to_ms INTEGER,
    confidence FLOAT
)
"""

SPEAKER_CORRECTION_EVENTS_SCHEMA = """
CREATE TABLE speaker_correction_events (
    id INTEGER PRIMARY KEY,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    public_id VARCHAR(36) NOT NULL,
    recording_id INTEGER NOT NULL,
    actor_user_id INTEGER,
    utterance_id INTEGER,
    source_recording_speaker_id INTEGER,
    target_recording_speaker_id INTEGER,
    target_global_speaker_id INTEGER,
    event_type VARCHAR(64) NOT NULL,
    scope VARCHAR(64) NOT NULL,
    effective_from_ms INTEGER,
    payload JSON
)
"""

PEOPLE_TAGS_SCHEMA = """
CREATE TABLE people_tags (
    id INTEGER PRIMARY KEY,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    tag_id INTEGER,
    global_speaker_id INTEGER
)
"""

P_TAGS_SCHEMA = """
CREATE TABLE p_tags (
    id INTEGER PRIMARY KEY,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    name VARCHAR(255) NOT NULL,
    color VARCHAR(32),
    user_id INTEGER,
    parent_id INTEGER
)
"""
