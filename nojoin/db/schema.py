# nojoin/db/schema.py

CREATE_RECORDINGS_TABLE = """
CREATE TABLE IF NOT EXISTS recordings (
    id TEXT PRIMARY KEY, -- Format: YYYYMMDDHHSS
    name TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    processed_at DATETIME,
    start_time DATETIME, -- Meeting start time (optional, for future use)
    end_time DATETIME,   -- Meeting end time (optional, for future use)
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
    voice_snippet_path TEXT, -- Optional path to a reference snippet
    global_speaker_id INTEGER, -- Optional link to a global speaker profile
    FOREIGN KEY (global_speaker_id) REFERENCES global_speakers (id) ON DELETE SET NULL
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

# --- New Global Speakers Table ---
CREATE_GLOBAL_SPEAKERS_TABLE = """
CREATE TABLE IF NOT EXISTS global_speakers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE COLLATE NOCASE NOT NULL
);
"""

# Indexes for potentially queried columns
CREATE_RECORDINGS_NAME_INDEX = "CREATE INDEX IF NOT EXISTS idx_recordings_name ON recordings (name);"
CREATE_RECORDINGS_CREATED_AT_INDEX = "CREATE INDEX IF NOT EXISTS idx_recordings_created_at ON recordings (created_at);"
CREATE_RECORDING_SPEAKERS_SPEAKER_ID_INDEX = "CREATE INDEX IF NOT EXISTS idx_recording_speakers_speaker_id ON recording_speakers (speaker_id);"
# --- New Indexes for Global Speakers and linking ---
CREATE_GLOBAL_SPEAKERS_NAME_INDEX = "CREATE INDEX IF NOT EXISTS idx_global_speakers_name ON global_speakers (name);"
CREATE_SPEAKERS_GLOBAL_SPEAKER_ID_INDEX = "CREATE INDEX IF NOT EXISTS idx_speakers_global_speaker_id ON speakers (global_speaker_id);"


SCHEMA_STATEMENTS = [
    CREATE_RECORDINGS_TABLE,
    CREATE_GLOBAL_SPEAKERS_TABLE, # Add before speakers table due to FK
    CREATE_SPEAKERS_TABLE,
    CREATE_RECORDING_SPEAKERS_TABLE,
    CREATE_TAGS_TABLE,
    CREATE_RECORDING_TAGS_TABLE,
    CREATE_MEETING_NOTES_TABLE,
    CREATE_RECORDINGS_NAME_INDEX,
    CREATE_RECORDINGS_CREATED_AT_INDEX,
    CREATE_RECORDING_SPEAKERS_SPEAKER_ID_INDEX,
    CREATE_GLOBAL_SPEAKERS_NAME_INDEX, # Add new index
    CREATE_SPEAKERS_GLOBAL_SPEAKER_ID_INDEX # Add new index
] 