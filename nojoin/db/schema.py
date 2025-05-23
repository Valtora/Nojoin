# nojoin/db/schema.py

CREATE_RECORDINGS_TABLE = """
CREATE TABLE IF NOT EXISTS recordings (
    id TEXT PRIMARY KEY, -- Format: YYYYMMDDHHSS
    name TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    processed_at DATETIME,
    audio_path TEXT NOT NULL UNIQUE,
    raw_transcript_path TEXT,
    diarized_transcript_path TEXT,
    tags TEXT, -- Comma-separated or JSON list
    format TEXT DEFAULT 'MP3',
    duration_seconds REAL,
    file_size_bytes INTEGER,
    status TEXT DEFAULT 'Recorded' NOT NULL CHECK(status IN ('Recorded', 'Processing', 'Processed', 'Error')),
    chat_history TEXT -- JSON blob for persistent meeting chat
); 
"""

CREATE_SPEAKERS_TABLE = """
CREATE TABLE IF NOT EXISTS speakers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    voice_snippet_path TEXT -- Optional path to a reference snippet
);
"""

CREATE_RECORDING_SPEAKERS_TABLE = """
CREATE TABLE IF NOT EXISTS recording_speakers (
    recording_id INTEGER NOT NULL,
    speaker_id INTEGER NOT NULL,
    diarization_label TEXT NOT NULL, -- e.g., SPEAKER_00, SPEAKER_01
    snippet_start REAL, -- Start time (seconds) of clearest segment
    snippet_end REAL,   -- End time (seconds) of clearest segment
    FOREIGN KEY (recording_id) REFERENCES recordings (id) ON DELETE CASCADE,
    FOREIGN KEY (speaker_id) REFERENCES speakers (id) ON DELETE CASCADE,
    PRIMARY KEY (recording_id, diarization_label) -- Ensures a diarization label is unique within a recording
);
"""

CREATE_TAGS_TABLE = """
CREATE TABLE IF NOT EXISTS tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE COLLATE NOCASE NOT NULL
);
"""

CREATE_RECORDING_TAGS_TABLE = """
CREATE TABLE IF NOT EXISTS recording_tags (
    recording_id TEXT NOT NULL,
    tag_id INTEGER NOT NULL,
    FOREIGN KEY (recording_id) REFERENCES recordings(id) ON DELETE CASCADE,
    FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY (recording_id, tag_id)
);
"""

CREATE_MEETING_NOTES_TABLE = """
CREATE TABLE IF NOT EXISTS meeting_notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recording_id TEXT NOT NULL,
    llm_backend TEXT NOT NULL,
    model TEXT NOT NULL,
    notes TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME,
    FOREIGN KEY (recording_id) REFERENCES recordings(id) ON DELETE CASCADE
);
"""

# Indexes for potentially queried columns
CREATE_RECORDINGS_NAME_INDEX = "CREATE INDEX IF NOT EXISTS idx_recordings_name ON recordings (name);"
CREATE_RECORDINGS_CREATED_AT_INDEX = "CREATE INDEX IF NOT EXISTS idx_recordings_created_at ON recordings (created_at);"
CREATE_RECORDING_SPEAKERS_SPEAKER_ID_INDEX = "CREATE INDEX IF NOT EXISTS idx_recording_speakers_speaker_id ON recording_speakers (speaker_id);"


SCHEMA_STATEMENTS = [
    CREATE_RECORDINGS_TABLE,
    CREATE_SPEAKERS_TABLE,
    CREATE_RECORDING_SPEAKERS_TABLE,
    CREATE_TAGS_TABLE,
    CREATE_RECORDING_TAGS_TABLE,
    CREATE_MEETING_NOTES_TABLE,
    CREATE_RECORDINGS_NAME_INDEX,
    CREATE_RECORDINGS_CREATED_AT_INDEX,
    CREATE_RECORDING_SPEAKERS_SPEAKER_ID_INDEX
] 