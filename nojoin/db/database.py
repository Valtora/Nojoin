# nojoin/db/database.py
# Note: recording_id is always a string in the format YYYYMMDDHHMMSS (see schema.py)

import sqlite3
import logging
import os
from .schema import SCHEMA_STATEMENTS
from datetime import datetime
import re
from ..utils.config_manager import to_project_relative_path, from_project_relative_path, get_db_path, migrate_file_if_needed, get_project_root

logger = logging.getLogger(__name__)

# Define database path (now in the nojoin directory)
DB_NAME = 'nojoin_data.db'
DB_PATH = get_db_path()
# Migrate old DB if needed
old_db_path = os.path.abspath(os.path.join(get_project_root(), DB_NAME))
migrate_file_if_needed(old_db_path, DB_PATH)

def get_db_connection():
    """Establishes and returns a connection to the SQLite database."""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row # Return rows as dictionary-like objects
        conn.execute("PRAGMA foreign_keys = ON;") # Enforce foreign key constraints
        logger.debug(f"Database connection established to {DB_PATH}")
        return conn
    except sqlite3.Error as e:
        logger.error(f"Error connecting to database {DB_PATH}: {e}", exc_info=True)
        raise # Re-raise the exception to be handled upstream

def init_db():
    """Initializes the database by creating tables if they don't exist and ensures chat_history column exists."""
    logger.info(f"Initializing database at {DB_PATH}...")
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            for statement in SCHEMA_STATEMENTS:
                logger.debug(f"Executing schema: {statement.strip()}")
                cursor.execute(statement)
            conn.commit()
            logger.info("Database schema initialized successfully.")
        ensure_chat_history_column()
    except sqlite3.Error as e:
        logger.error(f"Error initializing database schema: {e}", exc_info=True)
    except Exception as e:
        logger.error(f"An unexpected error occurred during database initialization: {e}", exc_info=True)

# --- Basic CRUD Operations --- 

# We will add functions for Recordings, Speakers, and RecordingSpeakers here later.

# Example: Function to add a new recording (will be expanded)
def add_recording(name: str, audio_path: str, duration: float, size_bytes: int, format: str = "MP3", chat_history: str = None):
    """Adds a new recording entry to the database, optionally with chat_history."""
    audio_path = to_project_relative_path(from_project_relative_path(audio_path))
    sql = '''INSERT INTO recordings(id, name, audio_path, duration_seconds, file_size_bytes, status, format, chat_history)
             VALUES(?, ?, ?, ?, ?, ?, ?, ?)'''
    status = 'Recorded'
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            recording_id = datetime.now().strftime('%Y%m%d%H%M%S')
            cursor.execute(sql, (recording_id, name, audio_path, duration, size_bytes, status, format, chat_history))
            conn.commit()
            logger.info(f"Added recording '{name}' with path '{audio_path}'. ID: {recording_id}")
            return recording_id
    except sqlite3.IntegrityError as e:
        logger.error(f"Integrity error adding recording '{name}': {e}", exc_info=True)
        return None
    except sqlite3.Error as e:
        logger.error(f"Database error adding recording '{name}': {e}", exc_info=True)
        return None

def get_all_recordings():
    """Retrieves all recordings from the database, ordered by creation date descending."""
    sql = '''SELECT id, name, created_at, duration_seconds, status,
                   audio_path, raw_transcript_path, diarized_transcript_path
             FROM recordings
             ORDER BY created_at DESC''' # Show newest first
    recordings = []
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(sql)
            recordings = cursor.fetchall() # Fetch all rows as Row objects
            logger.debug(f"Retrieved {len(recordings)} recordings from database.")
    except sqlite3.Error as e:
        logger.error(f"Database error retrieving recordings: {e}", exc_info=True)
        # Return empty list on error, UI should handle this
    return recordings

def update_recording_status(recording_id: str, status: str):
    """Updates the status of a specific recording."""
    recording_id = str(recording_id)
    sql = "UPDATE recordings SET status = ?, processed_at = CASE WHEN ? = 'Processed' THEN CURRENT_TIMESTAMP ELSE processed_at END WHERE id = ?"
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, (status, status, recording_id))
            conn.commit()
            if cursor.rowcount == 0:
                logger.warning(f"Attempted to update status for non-existent recording ID: {recording_id}")
                return False
            else:
                logger.info(f"Updated status for recording ID {recording_id} to '{status}'.")
                return True
    except sqlite3.Error as e:
        logger.error(f"Database error updating status for recording ID {recording_id}: {e}", exc_info=True)
        return False

def delete_recording(recording_id: str):
    """Deletes a recording entry from the database by its ID and removes associated transcript files."""
    recording_id = str(recording_id)
    sql = 'DELETE FROM recordings WHERE id = ?'
    try:
        # Get file paths before deleting from DB
        rec = get_recording_by_id(recording_id)
        raw_path = rec.get('raw_transcript_path') if rec else None
        diarized_path = rec.get('diarized_transcript_path') if rec else None
        audio_path = rec.get('audio_path') if rec else None
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, (recording_id,))
            conn.commit()
            if cursor.rowcount == 0:
                logger.warning(f"Attempted to delete non-existent recording ID: {recording_id}")
                return False
            else:
                logger.info(f"Deleted recording ID {recording_id} from database.")
                # Remove associated files
                import os
                for path in [raw_path, diarized_path, audio_path]:
                    if path:
                        abs_path = from_project_relative_path(path)
                        if os.path.exists(abs_path):
                            try:
                                os.remove(abs_path)
                                logger.info(f"Deleted file: {abs_path}")
                            except Exception as e:
                                logger.warning(f"Failed to delete file {abs_path}: {e}")
                return True
    except sqlite3.Error as e:
        logger.error(f"Database error deleting recording ID {recording_id}: {e}", exc_info=True)
        return False

def update_recording_paths(recording_id: str, raw_transcript_path: str | None = None, diarized_transcript_path: str | None = None):
    """Updates the file paths associated with a recording."""
    recording_id = str(recording_id)
    updates = []
    params = []
    if raw_transcript_path:
        raw_transcript_path = to_project_relative_path(from_project_relative_path(raw_transcript_path))
        updates.append("raw_transcript_path = ?")
        params.append(raw_transcript_path)
    if diarized_transcript_path:
        diarized_transcript_path = to_project_relative_path(from_project_relative_path(diarized_transcript_path))
        updates.append("diarized_transcript_path = ?")
        params.append(diarized_transcript_path)

    if not updates:
        logger.warning("update_recording_paths called with no paths to update.")
        return False

    sql = f'UPDATE recordings SET {", ".join(updates)} WHERE id = ?'
    params.append(recording_id)

    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, tuple(params))
            conn.commit()
            if cursor.rowcount == 0:
                logger.warning(f"Attempted to update paths for non-existent recording ID: {recording_id}")
                return False
            else:
                logger.info(f"Updated paths for recording ID {recording_id}.")
                return True
    except sqlite3.Error as e:
        logger.error(f"Database error updating paths for recording ID {recording_id}: {e}", exc_info=True)
        return False

def update_recording_name(recording_id: str, new_name: str):
    """Update the name of a recording by its ID."""
    recording_id = str(recording_id)
    sql = "UPDATE recordings SET name = ? WHERE id = ?"
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, (new_name, recording_id))
            conn.commit()
            if cursor.rowcount == 0:
                logger.warning(f"Attempted to update name for non-existent recording ID: {recording_id}")
                return False
            else:
                logger.info(f"Updated name for recording ID {recording_id} to '{new_name}'")
                return True
    except sqlite3.Error as e:
        logger.error(f"Database error updating name for recording ID {recording_id}: {e}", exc_info=True)
        return False

def update_recording_tags(recording_id: str, tags: str):
    """
    Updates the tags for a specific recording.
    Args:
        recording_id: The ID of the recording.
        tags: The new tags string (comma separated).
    Returns:
        True if successful, False otherwise.
    """
    recording_id = str(recording_id)
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE recordings SET tags = ? WHERE id = ?", (tags, recording_id))
            conn.commit()
            if cursor.rowcount == 0:
                logger.warning(f"Attempted to update tags for non-existent recording ID: {recording_id}")
                return False
            else:
                logger.info(f"Updated tags for recording ID {recording_id} to '{tags}'")
                return True
    except sqlite3.Error as e:
        logger.error(f"Database error updating tags for recording ID {recording_id}: {e}", exc_info=True)
        return False

# --- Speaker Management Functions ---

def get_or_create_speaker(name: str):
    """
    Always creates a new speaker entry with the given name.
    Args:
        name: The name of the speaker to create.
    Returns:
        A dictionary with the speaker's id and name, or None on error.
    """
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("INSERT INTO speakers (name) VALUES (?)", (name,))
            conn.commit()
            speaker_id = cursor.lastrowid
            logger.info(f"Created new speaker '{name}' with ID {speaker_id}")
            return {"id": speaker_id, "name": name}
    except sqlite3.Error as e:
        logger.error(f"Database error in get_or_create_speaker for '{name}': {e}", exc_info=True)
        return None

def get_or_create_unknown_speaker(recording_id: str):
    """
    Ensures there is an 'Unknown' speaker for the given recording. Returns its speaker_id and name.
    """
    unknown_name = "Unknown"
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            # Check if 'Unknown' speaker exists globally
            cursor.execute("SELECT id FROM speakers WHERE name = ?", (unknown_name,))
            row = cursor.fetchone()
            if row:
                speaker_id = row['id']
            else:
                cursor.execute("INSERT INTO speakers (name) VALUES (?)", (unknown_name,))
                speaker_id = cursor.lastrowid
            # Check if associated with this recording
            cursor.execute("SELECT 1 FROM recording_speakers WHERE recording_id = ? AND speaker_id = ?", (recording_id, speaker_id))
            if not cursor.fetchone():
                # Use a unique diarization_label for 'Unknown' in this recording
                unknown_label = "Unknown"
                cursor.execute(
                    "INSERT INTO recording_speakers (recording_id, speaker_id, diarization_label) VALUES (?, ?, ?)",
                    (recording_id, speaker_id, unknown_label)
                )
            conn.commit()
            return speaker_id, unknown_name
    except Exception as e:
        logger.error(f"Error ensuring Unknown speaker for recording {recording_id}: {e}", exc_info=True)
        return None, unknown_name

def update_speaker_name(speaker_id: int, new_name: str, recording_id: str = None):
    """
    Updates a speaker's name and updates the transcript to use the new name for all associated diarization labels in the given recording.
    If recording_id is None, updates all recordings for this speaker.
    """
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE speakers SET name = ? WHERE id = ?", (new_name, speaker_id))
            # Update transcript(s)
            if recording_id:
                cursor.execute("SELECT diarization_label FROM recording_speakers WHERE recording_id = ? AND speaker_id = ?", (recording_id, speaker_id))
                rows = cursor.fetchall()
                for row in rows:
                    old_label = row['diarization_label']
                    replace_speaker_in_transcript(recording_id, old_label, new_name)
            else:
                # Update all recordings for this speaker
                cursor.execute("SELECT recording_id, diarization_label FROM recording_speakers WHERE speaker_id = ?", (speaker_id,))
                rows = cursor.fetchall()
                for row in rows:
                    replace_speaker_in_transcript(row['recording_id'], row['diarization_label'], new_name)
            conn.commit()
            logger.info(f"Updated name for speaker ID {speaker_id} to '{new_name}' and updated transcript(s)")
            return True
    except sqlite3.Error as e:
        logger.error(f"Database error updating speaker {speaker_id} to '{new_name}': {e}", exc_info=True)
        return False

def get_speakers_for_recording(recording_id: str):
    """
    Retrieves all speakers associated with a recording.
    
    Args:
        recording_id: The ID of the recording.
        
    Returns:
        A list of dictionaries containing speaker information including id, name, and diarization_label.
    """
    recording_id = str(recording_id)
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            sql = """
            SELECT s.id, s.name, rs.diarization_label
            FROM speakers s
            JOIN recording_speakers rs ON s.id = rs.speaker_id
            WHERE rs.recording_id = ?
            ORDER BY rs.diarization_label
            """
            cursor.execute(sql, (recording_id,))
            speakers = [dict(row) for row in cursor.fetchall()]
            logger.debug(f"Retrieved {len(speakers)} speakers for recording ID {recording_id}")
            return speakers
    except sqlite3.Error as e:
        logger.error(f"Database error retrieving speakers for recording ID {recording_id}: {e}", exc_info=True)
        return []

def associate_speaker_with_recording(recording_id: str, speaker_id: int, diarization_label: str):
    """
    Associates a speaker with a recording using a diarization label.
    
    Args:
        recording_id: The ID of the recording.
        speaker_id: The ID of the speaker.
        diarization_label: The diarization label (e.g., SPEAKER_00).
        
    Returns:
        True if successful, False otherwise.
    """
    recording_id = str(recording_id)
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            # Check if this diarization label is already associated with any speaker
            cursor.execute(
                "SELECT speaker_id FROM recording_speakers WHERE recording_id = ? AND diarization_label = ?",
                (recording_id, diarization_label)
            )
            existing = cursor.fetchone()
            
            if existing:
                # Update the existing association
                cursor.execute(
                    "UPDATE recording_speakers SET speaker_id = ? WHERE recording_id = ? AND diarization_label = ?",
                    (speaker_id, recording_id, diarization_label)
                )
            else:
                # Create a new association
                cursor.execute(
                    "INSERT INTO recording_speakers (recording_id, speaker_id, diarization_label) VALUES (?, ?, ?)",
                    (recording_id, speaker_id, diarization_label)
                )
            
            conn.commit()
            logger.info(f"Associated speaker ID {speaker_id} with recording ID {recording_id} as '{diarization_label}'")
            return True
    except sqlite3.Error as e:
        logger.error(f"Database error associating speaker {speaker_id} with recording {recording_id}: {e}", exc_info=True)
        return False

def get_speaker_by_id(speaker_id: int):
    """
    Retrieves a speaker by their ID.
    Args:
        speaker_id: The ID of the speaker.
    Returns:
        A dictionary with the speaker's id and name, or None if not found.
    """
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id, name FROM speakers WHERE id = ?", (speaker_id,))
            row = cursor.fetchone()
            if row:
                return dict(row)
            else:
                return None
    except sqlite3.Error as e:
        logger.error(f"Database error retrieving speaker by ID {speaker_id}: {e}", exc_info=True)
        return None

def delete_speaker_from_recording(recording_id: str, speaker_id: int) -> bool:
    """
    Removes a speaker from a specific recording. Reassigns transcript lines to 'Unknown'.
    """
    recording_id = str(recording_id)
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            # Get diarization_label(s) for this speaker in this recording
            cursor.execute("SELECT diarization_label FROM recording_speakers WHERE recording_id = ? AND speaker_id = ?", (recording_id, speaker_id))
            rows = cursor.fetchall()
            # Ensure 'Unknown' speaker exists for this recording
            unknown_id, unknown_name = get_or_create_unknown_speaker(recording_id)
            for row in rows:
                old_label = row['diarization_label']
                replace_speaker_in_transcript(recording_id, old_label, unknown_name)
            # Remove from recording_speakers
            cursor.execute("DELETE FROM recording_speakers WHERE recording_id = ? AND speaker_id = ?", (recording_id, speaker_id))
            # If speaker not used elsewhere, delete from speakers
            cursor.execute("SELECT COUNT(*) as cnt FROM recording_speakers WHERE speaker_id = ?", (speaker_id,))
            row = cursor.fetchone()
            if row and row['cnt'] == 0:
                cursor.execute("DELETE FROM speakers WHERE id = ?", (speaker_id,))
            conn.commit()
            return True
    except Exception as e:
        logger.error(f"Error deleting speaker {speaker_id} from recording {recording_id}: {e}", exc_info=True)
        return False

# --- Utility Functions ---
def get_current_timestamp_str():
     """Returns the current time as an ISO 8601 formatted string suitable for SQLite DATETIME."""
     return datetime.now().isoformat()

def extract_diarization_labels_from_transcript(transcript_text):
    """
    Extracts unique diarization labels (e.g., SPEAKER_00) from a diarized transcript text.
    Returns a list of unique labels.
    """
    # Regex to match 'Speaker_X' or 'SPEAKER_XX' after a bracketed timestamp
    pattern = re.compile(r"Speaker[_ ]?\d+|SPEAKER_\d+", re.IGNORECASE)
    labels = set()
    for match in pattern.findall(transcript_text):
        labels.add(match.upper())
    return sorted(labels)

def get_speaker_diarization_segments(recording_id: str, speaker_id: int):
    """
    Returns a dict with a list of segments (start_time, end_time) for the given speaker in the specified recording.
    Returns {'segments': [{'start_time': float, 'end_time': float}, ...]} or {'segments': []} if none found.
    """
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            # Find diarization label for this speaker in this recording
            cursor.execute("SELECT diarization_label FROM recording_speakers WHERE recording_id = ? AND speaker_id = ?", (recording_id, speaker_id))
            row = cursor.fetchone()
            if not row:
                return {'segments': []}
            diarization_label = row['diarization_label']
            # Find diarized transcript path
            cursor.execute("SELECT diarized_transcript_path FROM recordings WHERE id = ?", (recording_id,))
            rec_row = cursor.fetchone()
            if not rec_row or not rec_row['diarized_transcript_path']:
                return {'segments': []}
            transcript_path = rec_row['diarized_transcript_path']
            if not os.path.exists(transcript_path):
                return {'segments': []}
            # Parse transcript for segments
            segments = []
            import re
            with open(transcript_path, 'r', encoding='utf-8') as f:
                for line in f:
                    # Example line: [00.00.00 - 00.00.05] - SPEAKER_00. text
                    m = re.match(r"\[(\d+)\.(\d+)\.(\d+\.\d+) - (\d+)\.(\d+)\.(\d+\.\d+)\] - (\w+)[.:]", line)
                    if m:
                        start = int(m.group(1))*3600 + int(m.group(2))*60 + float(m.group(3))
                        end = int(m.group(4))*3600 + int(m.group(5))*60 + float(m.group(6))
                        label = m.group(7)
                        if label.upper() == diarization_label.upper():
                            segments.append({'start_time': start, 'end_time': end})
            return {'segments': segments}
    except Exception as e:
        logger.error(f"Error getting diarization segments for speaker {speaker_id} in recording {recording_id}: {e}", exc_info=True)
        return {'segments': []}

def add_diarization_labels(recording_id: str, diarization_labels: list, snippet_segments: dict = None):
    """
    Adds diarization labels (e.g., SPEAKER_00) to the speakers and recording_speakers tables for a recording.
    Optionally, snippet_segments is a dict mapping label -> (start, end) for the clearest segment.
    Args:
        recording_id: The ID of the recording.
        diarization_labels: List of speaker labels (e.g., ['SPEAKER_00', 'SPEAKER_01'])
        snippet_segments: Optional dict {label: (start, end)}
    """
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            for label in diarization_labels:
                # Create a speaker entry if not exists
                cursor.execute("SELECT id FROM speakers WHERE name = ?", (label,))
                row = cursor.fetchone()
                if row:
                    speaker_id = row['id']
                else:
                    cursor.execute("INSERT INTO speakers (name) VALUES (?)", (label,))
                    speaker_id = cursor.lastrowid
                # Associate with recording, including snippet times if provided
                cursor.execute("SELECT 1 FROM recording_speakers WHERE recording_id = ? AND diarization_label = ?", (recording_id, label))
                if not cursor.fetchone():
                    snippet_start, snippet_end = (None, None)
                    if snippet_segments and label in snippet_segments:
                        snippet_start, snippet_end = snippet_segments[label]
                    cursor.execute(
                        "INSERT INTO recording_speakers (recording_id, speaker_id, diarization_label, snippet_start, snippet_end) VALUES (?, ?, ?, ?, ?)",
                        (recording_id, speaker_id, label, snippet_start, snippet_end)
                    )
                else:
                    # Update snippet times if provided
                    if snippet_segments and label in snippet_segments:
                        snippet_start, snippet_end = snippet_segments[label]
                        cursor.execute(
                            "UPDATE recording_speakers SET snippet_start = ?, snippet_end = ? WHERE recording_id = ? AND diarization_label = ?",
                            (snippet_start, snippet_end, recording_id, label)
                        )
            conn.commit()
            logger.info(f"Added diarization labels for recording {recording_id}: {diarization_labels} (with snippet segments if provided)")
    except Exception as e:
        logger.error(f"Error adding diarization labels for recording {recording_id}: {e}", exc_info=True)

def get_recording_by_id(recording_id: str):
    """Fetch a single recording by its ID. Returns a dict or None if not found. Includes chat_history."""
    recording_id = str(recording_id)
    sql = '''SELECT id, name, created_at, duration_seconds, status, audio_path, raw_transcript_path, diarized_transcript_path, tags, format, file_size_bytes, processed_at, chat_history
             FROM recordings WHERE id = ?'''
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, (recording_id,))
            row = cursor.fetchone()
            if row:
                result = dict(row)
                for key in ["audio_path", "raw_transcript_path", "diarized_transcript_path"]:
                    if result.get(key):
                        result[key] = to_project_relative_path(from_project_relative_path(result[key]))
                return result
            else:
                return None
    except sqlite3.Error as e:
        logger.error(f"Database error retrieving recording by ID {recording_id}: {e}", exc_info=True)
        return None

# --- Tag Management Functions ---
def add_tag(name: str):
    """Add a new tag (case-insensitive unique). Returns tag id or None if exists."""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (name.strip(),))
            conn.commit()
            cursor.execute("SELECT id FROM tags WHERE name = ? COLLATE NOCASE", (name.strip(),))
            row = cursor.fetchone()
            return row['id'] if row else None
    except sqlite3.Error as e:
        logger.error(f"Database error adding tag '{name}': {e}", exc_info=True)
        return None

def get_tag_by_name(name: str):
    """Fetch a tag by name (case-insensitive). Returns dict or None."""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id, name FROM tags WHERE name = ? COLLATE NOCASE", (name.strip(),))
            row = cursor.fetchone()
            return dict(row) if row else None
    except sqlite3.Error as e:
        logger.error(f"Database error fetching tag by name '{name}': {e}", exc_info=True)
        return None

def get_tags():
    """Return all tags as list of dicts."""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id, name FROM tags ORDER BY name COLLATE NOCASE")
            return [dict(row) for row in cursor.fetchall()]
    except sqlite3.Error as e:
        logger.error(f"Database error fetching all tags: {e}", exc_info=True)
        return []

def suggest_tags_by_prefix(prefix: str):
    """Suggest tags by prefix (case-insensitive). Returns list of dicts."""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id, name FROM tags WHERE name LIKE ? COLLATE NOCASE ORDER BY name", (prefix + '%',))
            return [dict(row) for row in cursor.fetchall()]
    except sqlite3.Error as e:
        logger.error(f"Database error suggesting tags by prefix '{prefix}': {e}", exc_info=True)
        return []

def delete_tag(tag_id: int):
    """Delete a tag by id. Also removes assignments."""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM tags WHERE id = ?", (tag_id,))
            conn.commit()
            return cursor.rowcount > 0
    except sqlite3.Error as e:
        logger.error(f"Database error deleting tag id {tag_id}: {e}", exc_info=True)
        return False

def assign_tag_to_recording(recording_id: str, tag_name: str):
    """Assign a tag (by name) to a recording. Creates tag if needed."""
    recording_id = str(recording_id)
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            # Ensure tag exists
            cursor.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (tag_name.strip(),))
            cursor.execute("SELECT id FROM tags WHERE name = ? COLLATE NOCASE", (tag_name.strip(),))
            tag_row = cursor.fetchone()
            if not tag_row:
                return False
            tag_id = tag_row['id']
            # Assign
            cursor.execute("INSERT OR IGNORE INTO recording_tags (recording_id, tag_id) VALUES (?, ?)", (recording_id, tag_id))
            conn.commit()
            return True
    except sqlite3.Error as e:
        logger.error(f"Database error assigning tag '{tag_name}' to recording {recording_id}: {e}", exc_info=True)
        return False

def unassign_tag_from_recording(recording_id: str, tag_name: str):
    """Remove a tag (by name) from a recording."""
    recording_id = str(recording_id)
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM tags WHERE name = ? COLLATE NOCASE", (tag_name.strip(),))
            tag_row = cursor.fetchone()
            if not tag_row:
                return False
            tag_id = tag_row['id']
            cursor.execute("DELETE FROM recording_tags WHERE recording_id = ? AND tag_id = ?", (recording_id, tag_id))
            conn.commit()
            return cursor.rowcount > 0
    except sqlite3.Error as e:
        logger.error(f"Database error unassigning tag '{tag_name}' from recording {recording_id}: {e}", exc_info=True)
        return False

def get_tags_for_recording(recording_id: str):
    """Return all tags (as dicts) for a given recording."""
    recording_id = str(recording_id)
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT t.id, t.name FROM tags t
                JOIN recording_tags rt ON t.id = rt.tag_id
                WHERE rt.recording_id = ?
                ORDER BY t.name COLLATE NOCASE
            """, (recording_id,))
            return [dict(row) for row in cursor.fetchall()]
    except sqlite3.Error as e:
        logger.error(f"Database error fetching tags for recording {recording_id}: {e}", exc_info=True)
        return []

def get_recordings_for_tag(tag_name: str):
    """Return all recordings (as dicts) for a given tag name."""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM tags WHERE name = ? COLLATE NOCASE", (tag_name.strip(),))
            tag_row = cursor.fetchone()
            if not tag_row:
                return []
            tag_id = tag_row['id']
            cursor.execute("""
                SELECT r.* FROM recordings r
                JOIN recording_tags rt ON r.id = rt.recording_id
                WHERE rt.tag_id = ?
                ORDER BY r.created_at DESC
            """, (tag_id,))
            return [dict(row) for row in cursor.fetchall()]
    except sqlite3.Error as e:
        logger.error(f"Database error fetching recordings for tag '{tag_name}': {e}", exc_info=True)
        return []

def migrate_tags_to_normalized_schema():
    """
    Migrates existing comma-separated tags in the recordings table to the new normalized tags/recording_tags schema.
    - For each recording, parse the tags column (if not null/empty).
    - For each tag, create it in the tags table if needed, then assign to the recording in recording_tags.
    - Does not delete the old tags column (for safety/rollback).
    """
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id, tags FROM recordings WHERE tags IS NOT NULL AND tags != ''")
            rows = cursor.fetchall()
            for row in rows:
                recording_id = row['id']
                tags_str = row['tags']
                tags = [t.strip() for t in tags_str.split(',') if t.strip()]
                for tag in tags:
                    # Insert tag if not exists
                    cursor.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (tag,))
                    cursor.execute("SELECT id FROM tags WHERE name = ? COLLATE NOCASE", (tag,))
                    tag_row = cursor.fetchone()
                    if tag_row:
                        tag_id = tag_row['id']
                        # Assign tag to recording
                        cursor.execute("INSERT OR IGNORE INTO recording_tags (recording_id, tag_id) VALUES (?, ?)", (recording_id, tag_id))
            conn.commit()
            logger.info(f"Migrated tags for {len(rows)} recordings to normalized schema.")
    except sqlite3.Error as e:
        logger.error(f"Error migrating tags to normalized schema: {e}", exc_info=True)

def replace_speaker_in_transcript(recording_id: str, old_label: str, new_label: str = None) -> bool:
    """
    In the diarized transcript for the recording, replace all occurrences of old_label with new_label (user-defined name or 'Unknown').
    If new_label is None, remove lines for old_label.
    Returns True on success, False on error.
    """
    recording_id = str(recording_id)
    try:
        rec = get_recording_by_id(recording_id)
        path = rec.get('diarized_transcript_path')
        if not path or not os.path.exists(path):
            logger.error(f"Transcript file not found for recording {recording_id}")
            return False
        with open(path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        import re
        label_pattern = re.compile(rf"(\[.*?\]\s*-\s*){re.escape(old_label)}([.:])", re.IGNORECASE)
        new_lines = []
        for line in lines:
            if label_pattern.search(line):
                if new_label:
                    # Replace label
                    new_line = label_pattern.sub(rf"\1{new_label}\2", line)
                    new_lines.append(new_line)
                # else: skip line (delete)
            else:
                new_lines.append(line)
        with open(path, 'w', encoding='utf-8') as f:
            f.writelines(new_lines)
        return True
    except Exception as e:
        logger.error(f"Error replacing speaker label in transcript for recording {recording_id}: {e}", exc_info=True)
        return False

def merge_speakers_in_recording(recording_id: str, speaker_ids: list, target_speaker_id: int) -> bool:
    """
    Merge multiple speakers into one in a recording. Updates transcript lines to the user-defined name of the target speaker, removes redundant speakers.
    """
    recording_id = str(recording_id)
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            # Get target speaker's current name
            cursor.execute("SELECT name FROM speakers WHERE id = ?", (target_speaker_id,))
            row = cursor.fetchone()
            if not row:
                return False
            target_name = row['name']
            # For each other speaker, update transcript and DB
            for sid in speaker_ids:
                if sid == target_speaker_id:
                    continue
                cursor.execute("SELECT diarization_label FROM recording_speakers WHERE recording_id = ? AND speaker_id = ?", (recording_id, sid))
                r = cursor.fetchone()
                if not r:
                    continue
                old_label = r['diarization_label']
                # Update transcript to use target_name
                replace_speaker_in_transcript(recording_id, old_label, target_name)
                # Remove from recording_speakers
                cursor.execute("DELETE FROM recording_speakers WHERE recording_id = ? AND speaker_id = ?", (recording_id, sid))
                # If speaker not used elsewhere, delete from speakers
                cursor.execute("SELECT COUNT(*) as cnt FROM recording_speakers WHERE speaker_id = ?", (sid,))
                rc = cursor.fetchone()
                if rc and rc['cnt'] == 0:
                    cursor.execute("DELETE FROM speakers WHERE id = ?", (sid,))
            conn.commit()
        return True
    except Exception as e:
        logger.error(f"Error merging speakers {speaker_ids} in recording {recording_id}: {e}", exc_info=True)
        return False

# --- Meeting Notes Management Functions ---
def add_meeting_notes(recording_id: str, llm_backend: str, model: str, notes: str):
    """
    Adds meeting notes for a recording. Returns the inserted row ID or None on error.
    """
    recording_id = str(recording_id)
    sql = '''INSERT INTO meeting_notes (recording_id, llm_backend, model, notes) VALUES (?, ?, ?, ?)'''
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, (recording_id, llm_backend, model, notes))
            conn.commit()
            return cursor.lastrowid
    except sqlite3.Error as e:
        logger.error(f"Database error adding meeting notes for recording {recording_id}: {e}", exc_info=True)
        return None

def get_meeting_notes_for_recording(recording_id: str):
    """
    Retrieves meeting notes for a specific recording. Returns a dict or None if not found.
    """
    recording_id = str(recording_id)
    sql = '''SELECT * FROM meeting_notes WHERE recording_id = ? ORDER BY created_at DESC LIMIT 1'''
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, (recording_id,))
            row = cursor.fetchone()
            return dict(row) if row else None
    except sqlite3.Error as e:
        logger.error(f"Database error retrieving meeting notes for recording {recording_id}: {e}", exc_info=True)
        return None

def update_meeting_notes(notes_id: int, new_notes: str):
    """
    Updates the notes content and updated_at timestamp for a meeting notes entry.
    """
    sql = '''UPDATE meeting_notes SET notes = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?'''
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, (new_notes, notes_id))
            conn.commit()
            return cursor.rowcount > 0
    except sqlite3.Error as e:
        logger.error(f"Database error updating meeting notes id {notes_id}: {e}", exc_info=True)
        return False

def delete_meeting_notes(notes_id: int):
    """
    Deletes a meeting notes entry by its ID.
    """
    sql = '''DELETE FROM meeting_notes WHERE id = ?'''
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, (notes_id,))
            conn.commit()
            return cursor.rowcount > 0
    except sqlite3.Error as e:
        logger.error(f"Database error deleting meeting notes id {notes_id}: {e}", exc_info=True)
        return False

# --- Initialization call ---
# Call init_db() once, perhaps when the application starts.
# For now, we can call it here to ensure the DB exists when this module is imported,
# although in a real app, explicit initialization at startup is better.
# init_db()

def get_chat_history_for_recording(recording_id: str):
    """Returns the chat_history JSON string for a recording, or None if not set."""
    recording_id = str(recording_id)
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT chat_history FROM recordings WHERE id = ?", (recording_id,))
            row = cursor.fetchone()
            return row[0] if row and row[0] is not None else None
    except sqlite3.OperationalError as e:
        if 'no such column: chat_history' in str(e):
            ensure_chat_history_column()
            # Retry after migration
            return get_chat_history_for_recording(recording_id)
        logger.error(f"Error getting chat_history for recording {recording_id}: {e}", exc_info=True)
        return None
    except Exception as e:
        logger.error(f"Error getting chat_history for recording {recording_id}: {e}", exc_info=True)
        return None

def set_chat_history_for_recording(recording_id: str, chat_history_json: str):
    """Sets the chat_history JSON string for a recording."""
    recording_id = str(recording_id)
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE recordings SET chat_history = ? WHERE id = ?", (chat_history_json, recording_id))
            conn.commit()
            return cursor.rowcount > 0
    except sqlite3.OperationalError as e:
        if 'no such column: chat_history' in str(e):
            ensure_chat_history_column()
            # Retry after migration
            return set_chat_history_for_recording(recording_id, chat_history_json)
        logger.error(f"Error setting chat_history for recording {recording_id}: {e}", exc_info=True)
        return False
    except Exception as e:
        logger.error(f"Error setting chat_history for recording {recording_id}: {e}", exc_info=True)
        return False

def clear_chat_history_for_recording(recording_id: str):
    """Clears the chat_history for a recording (sets to NULL)."""
    recording_id = str(recording_id)
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE recordings SET chat_history = NULL WHERE id = ?", (recording_id,))
            conn.commit()
            return cursor.rowcount > 0
    except sqlite3.OperationalError as e:
        if 'no such column: chat_history' in str(e):
            ensure_chat_history_column()
            # Retry after migration
            return clear_chat_history_for_recording(recording_id)
        logger.error(f"Error clearing chat_history for recording {recording_id}: {e}", exc_info=True)
        return False
    except Exception as e:
        logger.error(f"Error clearing chat_history for recording {recording_id}: {e}", exc_info=True)
        return False

def ensure_chat_history_column():
    """Ensures the chat_history column exists in the recordings table (for backward compatibility)."""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            # Check if chat_history column exists
            cursor.execute("PRAGMA table_info(recordings)")
            columns = [row[1] for row in cursor.fetchall()]
            if 'chat_history' not in columns:
                cursor.execute("ALTER TABLE recordings ADD COLUMN chat_history TEXT")
                conn.commit()
                logger.info("Added chat_history column to recordings table.")
    except Exception as e:
        logger.error(f"Error ensuring chat_history column: {e}", exc_info=True)