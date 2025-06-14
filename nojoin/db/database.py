# nojoin/db/database.py
# Note: recording_id is always a string in the format YYYYMMDDHHMMSS (see schema.py)

import sqlite3
import logging
import os
from .schema import SCHEMA_STATEMENTS
from datetime import datetime
import re
from ..utils.config_manager import to_project_relative_path, from_project_relative_path, get_db_path, migrate_file_if_needed, get_project_root
from ..utils.path_manager import path_manager

logger = logging.getLogger(__name__)

# Define database path (now in user data directory)
DB_NAME = 'nojoin_data.db'
DB_PATH = get_db_path()
# Migration is handled by PathManager on startup

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
        run_migrations()
    except sqlite3.Error as e:
        logger.error(f"Error initializing database schema: {e}", exc_info=True)
    except Exception as e:
        logger.error(f"An unexpected error occurred during database initialization: {e}", exc_info=True)

def run_migrations():
    """Run all pending migrations to ensure database is up to date."""
    logger.info("Running database migrations...")
    ensure_chat_history_column()
    ensure_global_speaker_columns()
    ensure_transcript_text_columns()
    migrate_transcripts_to_db()
    logger.info("Database migrations completed.")

# --- Basic CRUD Operations --- 

# We will add functions for Recordings, Speakers, and RecordingSpeakers here later.

# Example: Function to add a new recording (will be expanded)
def add_recording(name: str, audio_path: str, duration: float, size_bytes: int, format: str = "MP3", chat_history: str = None, start_time: str = None, end_time: str = None):
    """Adds a new recording entry to the database, optionally with chat_history, start_time, and end_time."""
    audio_path = to_project_relative_path(from_project_relative_path(audio_path))
    sql = '''INSERT INTO recordings(id, name, audio_path, duration_seconds, file_size_bytes, status, format, chat_history, start_time, end_time)
             VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)'''
    status = 'Recorded'
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            recording_id = datetime.now().strftime('%Y%m%d%H%M%S')
            cursor.execute(sql, (recording_id, name, audio_path, duration, size_bytes, status, format, chat_history, start_time, end_time))
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
                   audio_path, raw_transcript_path, diarized_transcript_path,
                   start_time, end_time
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
    logger.info(f"db_ops.delete_recording called for ID: {recording_id}")
    sql = 'DELETE FROM recordings WHERE id = ?'
    try:
        # Get file paths before deleting from DB
        rec = get_recording_by_id(recording_id)
        if not rec:
            logger.warning(f"db_ops.delete_recording: Recording ID {recording_id} not found in database.")
            return False

        raw_path = rec.get('raw_transcript_path')
        diarized_path = rec.get('diarized_transcript_path')
        audio_path = rec.get('audio_path')
        
        logger.info(f"db_ops.delete_recording: For ID {recording_id}, found audio_path: {audio_path}, raw_transcript_path: {raw_path}, diarized_transcript_path: {diarized_path}")

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, (recording_id,))
            conn.commit()
            if cursor.rowcount == 0:
                logger.warning(f"db_ops.delete_recording: Attempted to delete non-existent recording ID: {recording_id} from DB (was present moments before?).")
                # Even if DB delete fails unexpectedly, try to remove files if paths were fetched
            else:
                logger.info(f"db_ops.delete_recording: Successfully deleted recording ID {recording_id} from database.")

            # Remove associated files
            files_to_delete = {"audio file": audio_path, "raw transcript": raw_path, "diarized transcript": diarized_path}
            all_files_deleted_successfully = True

            for file_type, path in files_to_delete.items():
                if path:
                    abs_path = from_project_relative_path(path)
                    logger.info(f"db_ops.delete_recording: Attempting to delete {file_type}: {abs_path} for recording ID {recording_id}")
                    if os.path.exists(abs_path):
                        try:
                            logger.debug(f"db_ops.delete_recording: Calling os.remove() for {abs_path}")
                            os.remove(abs_path)
                            logger.info(f"db_ops.delete_recording: Successfully deleted {file_type}: {abs_path}")
                        except Exception as e:
                            logger.error(f"db_ops.delete_recording: Failed to delete {file_type} {abs_path}: {e}", exc_info=True)
                            all_files_deleted_successfully = False
                    else:
                        logger.warning(f"db_ops.delete_recording: {file_type} path {abs_path} does not exist, cannot delete.")
                else:
                    logger.debug(f"db_ops.delete_recording: No path for {file_type} for recording ID {recording_id}, skipping deletion.")
            
            return cursor.rowcount > 0 and all_files_deleted_successfully

    except sqlite3.Error as e:
        logger.error(f"db_ops.delete_recording: Database error for recording ID {recording_id}: {e}", exc_info=True)
        return False
    except Exception as e:
        logger.error(f"db_ops.delete_recording: Unexpected error for recording ID {recording_id}: {e}", exc_info=True)
        return False

def update_recording_transcript_text(recording_id: str, raw_transcript_text: str | None = None, diarized_transcript_text: str | None = None):
    """Updates the transcript text content for a recording."""
    from ..utils.transcript_store import TranscriptStore
    
    recording_id = str(recording_id)
    logger.info(f"Attempting to update transcript text for recording_id: {recording_id}")
    
    success = True
    if raw_transcript_text is not None:
        if not TranscriptStore.set(recording_id, raw_transcript_text, "raw"):
            success = False
            
    if diarized_transcript_text is not None:
        if not TranscriptStore.set(recording_id, diarized_transcript_text, "diarized"):
            success = False
    
    if success:
        logger.info(f"Successfully updated transcript text for recording ID {recording_id}")
    else:
        logger.error(f"Failed to update transcript text for recording ID {recording_id}")
    
    return success

def update_recording_paths(recording_id: str, raw_transcript_path: str | None = None, diarized_transcript_path: str | None = None):
    """Updates the file paths associated with a recording. DEPRECATED - Use update_recording_transcript_text instead."""
    recording_id = str(recording_id)
    logger.warning(f"update_recording_paths is deprecated. Consider using update_recording_transcript_text for recording {recording_id}")
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
            logger.debug(f"Executing SQL for update_recording_paths: {sql} with params: {params}")
            cursor.execute(sql, tuple(params))
            conn.commit()
            if cursor.rowcount == 0:
                logger.warning(f"Attempted to update paths for non-existent recording ID: {recording_id}")
                return False
            else:
                logger.info(f"Successfully updated paths for recording ID {recording_id}. Rowcount: {cursor.rowcount}")
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
            conn.commit()
            logger.info(f"Updated name for speaker ID {speaker_id} to '{new_name}'. Transcript file NOT modified by this operation.")
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
    Removes a speaker from a specific recording. Deletes all transcript lines attributed to that speaker by any diarization label or name (including user renames) in the diarized transcript file.
    """
    recording_id = str(recording_id)
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            # Get all diarization labels for this speaker in this recording
            cursor.execute("SELECT diarization_label FROM recording_speakers WHERE recording_id = ? AND speaker_id = ?", (recording_id, speaker_id))
            diarization_labels_rows = cursor.fetchall()
            diarization_labels = [row['diarization_label'] for row in diarization_labels_rows]
            
            # Get current name of the speaker from the 'speakers' table
            cursor.execute("SELECT name FROM speakers WHERE id = ?", (speaker_id,))
            speaker_name_row = cursor.fetchone()

            # Identifiers for this speaker in this recording are their diarization labels.
            labels_to_delete_from_transcript = set(diarization_labels)
            # if current_speaker_name and current_speaker_name not in diarization_labels: # Removed: transcript should only contain diarization labels
            #     labels_to_delete_from_transcript.add(current_speaker_name)
            
            if not labels_to_delete_from_transcript:
                logger.warning(f"No labels found to delete for speaker {speaker_id} in recording {recording_id}. Proceeding with DB deletion only.")
            else:
                # Remove all lines for all labels and names from the diarized transcript
                # The raw transcript is not modified by this function currently.
                if not replace_speaker_in_transcript(recording_id, list(labels_to_delete_from_transcript), None):
                    logger.error(f"Failed to update transcript while deleting speaker {speaker_id} from recording {recording_id}. Aborting DB changes.")
                    return False # Important: Do not proceed with DB deletion if transcript update fails

            # Remove from recording_speakers
            cursor.execute("DELETE FROM recording_speakers WHERE recording_id = ? AND speaker_id = ?", (recording_id, speaker_id))
            
            # If speaker not used elsewhere (across all recordings), delete from speakers table
            # This check should be if the speaker is linked in ANY recording_speakers entry.
            cursor.execute("SELECT COUNT(*) as cnt FROM recording_speakers WHERE speaker_id = ?", (speaker_id,))
            speaker_usage_count = cursor.fetchone()['cnt']
            
            if speaker_usage_count == 0:
                # Before deleting from speakers, check if it's linked to a global speaker.
                # If so, we might want to preserve the global speaker link or handle it based on product decision.
                # For now, we just delete the local speaker if unused. Global speaker remains.
                cursor.execute("DELETE FROM speakers WHERE id = ?", (speaker_id,))
                logger.info(f"Speaker ID {speaker_id} deleted from 'speakers' table as it's no longer associated with any recordings.")
            
            conn.commit()
            logger.info(f"Successfully deleted speaker {speaker_id} (and their transcript lines for labels: {labels_to_delete_from_transcript}) from recording {recording_id}.")
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
    from ..utils.transcript_store import TranscriptStore
    
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            # Find diarization label for this speaker in this recording
            cursor.execute("SELECT diarization_label FROM recording_speakers WHERE recording_id = ? AND speaker_id = ?", (recording_id, speaker_id))
            row = cursor.fetchone()
            if not row:
                logger.warning(f"No diarization label found for speaker {speaker_id} in recording {recording_id} within get_speaker_diarization_segments.")
                return {'segments': []}
            diarization_label = row['diarization_label']
            
            # Get transcript text from database
            transcript_text = TranscriptStore.get(recording_id, "diarized")
            if not transcript_text:
                logger.warning(f"No diarized transcript text found for recording {recording_id} within get_speaker_diarization_segments.")
                return {'segments': []}
                
            # Parse transcript for segments
            segments = []
            import re
            for line in transcript_text.split('\n'):
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
    sql = '''SELECT id, name, created_at, duration_seconds, status, audio_path, raw_transcript_path, diarized_transcript_path, tags, format, file_size_bytes, processed_at, chat_history, start_time, end_time
             FROM recordings WHERE id = ?'''
    logger.debug(f"Attempting to fetch recording by ID: {recording_id}")
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, (recording_id,))
            row = cursor.fetchone()
            if row:
                result = dict(row)
                logger.info(f"Successfully fetched recording ID {recording_id}. Data: {result}")
                # Ensure paths are consistently relative for the application logic
                for key in ["audio_path", "raw_transcript_path", "diarized_transcript_path"]:
                    if result.get(key):
                        # Paths are stored relative, ensure from_project_relative_path is used if an absolute one was somehow stored
                        # and to_project_relative_path to ensure it remains consistently relative for internal use.
                        # This double conversion handles cases where an absolute path might have been stored previously by mistake.
                        # The goal is for the application logic to primarily deal with relative paths, converting to absolute only when accessing files.
                        absolute_path_check = from_project_relative_path(result[key])
                        result[key] = to_project_relative_path(absolute_path_check) 
                return result
            else:
                logger.warning(f"No recording found for ID: {recording_id}")
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

def _replace_speaker_in_text(text: str, old_labels_to_find: list[str], new_name_for_transcript: str | None) -> tuple[str, int]:
    """
    Core text processing function that replaces speakers in transcript text.
    This is a pure function that operates on strings without file system operations.
    """
    if not old_labels_to_find or not text:
        return text, 0
        
    lines = text.split('\n')
    modified_lines = []
    total_replacements = 0
    
    for line_number, line in enumerate(lines):
        original_line = line
        modified_line_this_iteration = False

        # Regex to capture: (timestamp_prefix, full_speaker_part, text_suffix_with_separator)
        match = re.match(r"^(\[.+?\]\s*-\s*)(.+?)(\s*-\s*)(.*)$", line)

        if match:
            timestamp_prefix = match.group(1)
            current_speaker_part = match.group(2).strip()
            separator = match.group(3)
            text_content = match.group(4)

            # Handle each old_label to see if it's in the current_speaker_part
            for old_label in old_labels_to_find:
                escaped_old_label = re.escape(old_label)

                # Check if the old_label is part of the current_speaker_part (case-insensitive)
                if re.search(r"\b" + escaped_old_label + r"\b", current_speaker_part, re.IGNORECASE):
                    if new_name_for_transcript: # Renaming/Merging
                        components = [c.strip() for c in re.split(r'\s+and\s+', current_speaker_part)]
                        
                        new_components = []
                        replaced_in_components = False
                        for comp in components:
                            has_overlap_suffix = comp.endswith(" (Overlap)")
                            clean_comp = comp.removesuffix(" (Overlap)").strip() if has_overlap_suffix else comp
                            
                            if clean_comp.lower() == old_label.lower():
                                new_comp = new_name_for_transcript
                                if has_overlap_suffix:
                                    new_comp += " (Overlap)"
                                new_components.append(new_comp)
                                replaced_in_components = True
                            else:
                                new_components.append(comp)
                        
                        if replaced_in_components:
                            unique_new_components = []
                            for nc in new_components:
                                if nc and nc not in unique_new_components:
                                    unique_new_components.append(nc)
                            
                            current_speaker_part = " and ".join(unique_new_components)
                            modified_line_this_iteration = True
                        else: 
                            if current_speaker_part.lower() == old_label.lower():
                                current_speaker_part = new_name_for_transcript
                                modified_line_this_iteration = True

                    else: # Deleting speaker (new_name_for_transcript is None)
                        components = [c.strip() for c in re.split(r'\s+and\s+', current_speaker_part)]
                        remaining_components = []
                        overlap_suffix_present_originally = any("(Overlap)" in c for c in components)
                        
                        for comp in components:
                            clean_comp = comp.removesuffix(" (Overlap)").strip()
                            if clean_comp.lower() != old_label.lower():
                                remaining_components.append(comp)
                        
                        if len(remaining_components) < len(components):
                            if not remaining_components:
                                line = ""  # Remove line entirely if no speakers left
                            else:
                                current_speaker_part = " and ".join(remaining_components)
                                if len(remaining_components) == 1 and overlap_suffix_present_originally and not current_speaker_part.endswith(" (Overlap)"):
                                    current_speaker_part += " (Overlap)"
                            modified_line_this_iteration = True
                        else:
                            if current_speaker_part.lower() == old_label.lower():
                                line = ""  # Remove line
                                modified_line_this_iteration = True
                    
                    # Reconstruct line if it wasn't blanked out
                    if line and modified_line_this_iteration:
                        line = f"{timestamp_prefix}{current_speaker_part}{separator}{text_content}"
                    break
            
            if modified_line_this_iteration:
                total_replacements += 1
        
        # Add the line to results if it's not empty
        if line.strip():
            modified_lines.append(line)
        elif original_line.strip() and not line.strip():
            total_replacements += 1  # Count deletions as replacements

    return '\n'.join(modified_lines), total_replacements

def replace_speaker_in_transcript(recording_id: str, old_labels_to_find: list[str], new_name_for_transcript: str | None) -> bool:
    """
    Replace speakers in the diarized transcript stored in the database.
    
    Args:
        recording_id: The ID of the recording.
        old_labels_to_find: A list of speaker identifiers to replace or remove.
        new_name_for_transcript: The new name to use in the transcript. If None, segments are removed.
    Returns:
        True on success, False on error.
    """
    from ..utils.transcript_store import TranscriptStore
    
    recording_id = str(recording_id)
    if not old_labels_to_find:
        logger.warning("replace_speaker_in_transcript called with no old_labels_to_find.")
        return False

    try:
        def replacement_fn(text):
            return _replace_speaker_in_text(text, old_labels_to_find, new_name_for_transcript)
        
        replacements = TranscriptStore.replace(recording_id, replacement_fn, "diarized")
        if replacements >= 0:
            logger.info(f"Successfully made {replacements} replacements in transcript for recording {recording_id}")
            return True
        else:
            logger.error(f"Failed to update transcript for recording {recording_id}")
            return False
            
    except Exception as e:
        logger.error(f"Error replacing speaker in transcript for recording {recording_id}: {e}", exc_info=True)
        return False

def merge_speakers_in_recording(recording_id: str, speaker_ids: list[int], target_speaker_id: int) -> bool:
    """
    Merge multiple speakers into one in a recording. Updates transcript lines to the user-defined name of the target speaker, removes redundant speakers.
    """
    recording_id = str(recording_id)
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            # Get target speaker's current name AND diarization label for this recording
            cursor.execute("SELECT s.name, rs.diarization_label FROM speakers s JOIN recording_speakers rs ON s.id = rs.speaker_id WHERE s.id = ? AND rs.recording_id = ?", 
                           (target_speaker_id, recording_id))
            target_speaker_info = cursor.fetchone()
            if not target_speaker_info:
                logger.error(f"[merge_speakers] Target speaker {target_speaker_id} not found or not associated with recording {recording_id} for merge.")
                return False
            # target_name = target_speaker_info['name'] # We still need this for logging or other purposes if any
            target_diarization_label = target_speaker_info['diarization_label']
            logger.info(f"[merge_speakers] Target for merge: ID={target_speaker_id}, Name={target_speaker_info['name']}, Label={target_diarization_label}")

            # For each other speaker, update transcript and DB
            for sid in speaker_ids:
                if sid == target_speaker_id:
                    continue
                cursor.execute("SELECT diarization_label FROM recording_speakers WHERE recording_id = ? AND speaker_id = ?", (recording_id, sid))
                r = cursor.fetchone()
                if not r:
                    continue
                old_label = r['diarization_label']
                logger.info(f"[merge_speakers] Merging SID={sid} (Old Label: {old_label}) into Target Label: {target_diarization_label}")
                # Update transcript to use target_diarization_label
                if not replace_speaker_in_transcript(recording_id, [old_label], target_diarization_label):
                    logger.error(f"[merge_speakers] Failed to update transcript while merging {old_label} to {target_diarization_label} for recording {recording_id}. Aborting merge for this speaker.")
                    # Decide on error handling: continue with other speakers or rollback/return False?
                    # For now, let's be strict and abort if any transcript update fails.
                    conn.rollback() # Rollback any DB changes made in this transaction for this merge
                    return False
                
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
        # Attempt to rollback if an exception occurs mid-transaction
        # However, get_db_connection() context manager handles commit/rollback on exit based on exception.
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

def delete_unknown_speaker_from_recording(recording_id: str) -> bool:
    """
    Removes the 'Unknown' speaker bin from a specific recording. Deletes all transcript lines attributed to 'Unknown'.
    """
    recording_id = str(recording_id)
    unknown_name = "Unknown"
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            # Find the 'Unknown' speaker_id for this recording
            cursor.execute("""
                SELECT s.id, rs.diarization_label FROM speakers s
                JOIN recording_speakers rs ON s.id = rs.speaker_id
                WHERE rs.recording_id = ? AND s.name = ?
            """, (recording_id, unknown_name))
            row = cursor.fetchone()
            if not row:
                return False  # No unknown speaker for this recording
            unknown_id = row['id']
            unknown_label = row['diarization_label']
            # Remove all transcript lines for 'Unknown'
            replace_speaker_in_transcript(recording_id, [unknown_label], None)
            # Remove from recording_speakers
            cursor.execute("DELETE FROM recording_speakers WHERE recording_id = ? AND speaker_id = ?", (recording_id, unknown_id))
            # If 'Unknown' speaker not used elsewhere, delete from speakers
            cursor.execute("SELECT COUNT(*) as cnt FROM recording_speakers WHERE speaker_id = ?", (unknown_id,))
            rc = cursor.fetchone()
            if rc and rc['cnt'] == 0:
                cursor.execute("DELETE FROM speakers WHERE id = ?", (unknown_id,))
            conn.commit()
            return True
    except Exception as e:
        logger.error(f"Error deleting 'Unknown' speaker from recording {recording_id}: {e}", exc_info=True)
        return False

# --- New: Global Speaker Library CRUD --- 
def add_global_speaker(name: str) -> int | None:
    """Adds a new speaker to the global library. Returns ID or None if error/exists."""
    sql = "INSERT OR IGNORE INTO global_speakers (name) VALUES (?)"
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, (name.strip(),))
            conn.commit()
            if cursor.lastrowid == 0: # Name likely already exists due to UNIQUE constraint
                # Fetch existing ID
                cursor.execute("SELECT id FROM global_speakers WHERE name = ? COLLATE NOCASE", (name.strip(),))
                row = cursor.fetchone()
                return row['id'] if row else None
            return cursor.lastrowid
    except sqlite3.Error as e:
        logger.error(f"DB error adding global speaker '{name}': {e}", exc_info=True)
        return None

def get_global_speaker_by_id(global_speaker_id: int) -> dict | None:
    """Fetches a global speaker by ID."""
    sql = "SELECT id, name FROM global_speakers WHERE id = ?"
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, (global_speaker_id,))
            row = cursor.fetchone()
            return dict(row) if row else None
    except sqlite3.Error as e:
        logger.error(f"DB error fetching global speaker ID {global_speaker_id}: {e}", exc_info=True)
        return None

def get_global_speaker_by_name(name: str) -> dict | None:
    """Fetches a global speaker by name (case-insensitive)."""
    sql = "SELECT id, name FROM global_speakers WHERE name = ? COLLATE NOCASE"
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, (name.strip(),))
            row = cursor.fetchone()
            return dict(row) if row else None
    except sqlite3.Error as e:
        logger.error(f"DB error fetching global speaker by name '{name}': {e}", exc_info=True)
        return None

def get_all_global_speakers() -> list[dict]:
    """Retrieves all global speakers, ordered by name."""
    sql = "SELECT id, name FROM global_speakers ORDER BY name COLLATE NOCASE"
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(sql)
            return [dict(row) for row in cursor.fetchall()]
    except sqlite3.Error as e:
        logger.error(f"DB error fetching all global speakers: {e}", exc_info=True)
        return []

def update_global_speaker_name(global_speaker_id: int, new_name: str) -> bool:
    """Updates a global speaker's name."""
    sql = "UPDATE global_speakers SET name = ? WHERE id = ?"
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, (new_name.strip(), global_speaker_id))
            conn.commit()
            return cursor.rowcount > 0
    except sqlite3.Error as e:
        logger.error(f"DB error updating global speaker ID {global_speaker_id}: {e}", exc_info=True)
        return False

def delete_global_speaker(global_speaker_id: int) -> bool:
    """Deletes a global speaker. Associated speakers in 'speakers' table will have their global_speaker_id set to NULL."""
    sql = "DELETE FROM global_speakers WHERE id = ?"
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            # Note: FK constraint on speakers.global_speaker_id is ON DELETE SET NULL
            cursor.execute(sql, (global_speaker_id,))
            conn.commit()
            return cursor.rowcount > 0
    except sqlite3.Error as e:
        logger.error(f"DB error deleting global speaker ID {global_speaker_id}: {e}", exc_info=True)
        return False

def link_speaker_to_global(speaker_id: int, global_speaker_id: int) -> bool:
    """Links a specific speaker (from 'speakers' table) to a global speaker profile."""
    sql = "UPDATE speakers SET global_speaker_id = ? WHERE id = ?"
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, (global_speaker_id, speaker_id))
            conn.commit()
            return cursor.rowcount > 0
    except sqlite3.Error as e:
        logger.error(f"DB error linking speaker ID {speaker_id} to global ID {global_speaker_id}: {e}", exc_info=True)
        return False

def unlink_speaker_from_global(speaker_id: int) -> bool:
    """Removes the link between a specific speaker and their global profile."""
    sql = "UPDATE speakers SET global_speaker_id = NULL WHERE id = ?"
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, (speaker_id,))
            conn.commit()
            return cursor.rowcount > 0
    except sqlite3.Error as e:
        logger.error(f"DB error unlinking speaker ID {speaker_id} from global profile: {e}", exc_info=True)
        return False

def get_speakers_linked_to_global(global_speaker_id: int) -> list[dict]:
    """Gets all specific speaker entries linked to a given global_speaker_id."""
    sql = "SELECT id, name, voice_snippet_path FROM speakers WHERE global_speaker_id = ?"
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, (global_speaker_id,))
            return [dict(row) for row in cursor.fetchall()]
    except sqlite3.Error as e:
        logger.error(f"DB error fetching speakers linked to global ID {global_speaker_id}: {e}", exc_info=True)
        return []

def get_speaker_with_global_info(speaker_id: int) -> dict | None:
    """Retrieves a speaker by their ID, including their linked global speaker name if available."""
    sql = """
    SELECT s.id, s.name, s.voice_snippet_path, s.global_speaker_id, gs.name as global_speaker_name
    FROM speakers s
    LEFT JOIN global_speakers gs ON s.global_speaker_id = gs.id
    WHERE s.id = ?
    """
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, (speaker_id,))
            row = cursor.fetchone()
            return dict(row) if row else None
    except sqlite3.Error as e:
        logger.error(f"Database error retrieving speaker with global info by ID {speaker_id}: {e}", exc_info=True)
        return None

def ensure_global_speaker_columns():
    """Ensures the global_speaker_id column exists in the speakers table (for backward compatibility)."""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            # Check if global_speaker_id column exists in speakers table
            cursor.execute("PRAGMA table_info(speakers)")
            columns = [row[1] for row in cursor.fetchall()]
            if 'global_speaker_id' not in columns:
                cursor.execute("ALTER TABLE speakers ADD COLUMN global_speaker_id INTEGER REFERENCES global_speakers(id) ON DELETE SET NULL")
                conn.commit()
                logger.info("Added global_speaker_id column to speakers table.")
    except Exception as e:
        logger.error(f"Error ensuring global_speaker_id column in speakers table: {e}", exc_info=True)

def ensure_transcript_text_columns():
    """Ensures the transcript text columns exist in the recordings table (for backward compatibility)."""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            # Check which transcript text columns exist
            cursor.execute("PRAGMA table_info(recordings)")
            columns = [row[1] for row in cursor.fetchall()]
            
            if 'raw_transcript_text' not in columns:
                cursor.execute("ALTER TABLE recordings ADD COLUMN raw_transcript_text TEXT")
                logger.info("Added raw_transcript_text column to recordings table.")
                
            if 'diarized_transcript_text' not in columns:
                cursor.execute("ALTER TABLE recordings ADD COLUMN diarized_transcript_text TEXT")
                logger.info("Added diarized_transcript_text column to recordings table.")
                
            conn.commit()
    except Exception as e:
        logger.error(f"Error ensuring transcript text columns: {e}", exc_info=True)

def migrate_transcripts_to_db():
    """
    Migrates existing transcript files to database storage.
    For each recording with file paths but no text content:
    1. Read the file content
    2. Store in the appropriate text column  
    3. Delete the file
    4. Clear the path column
    """
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            # Find recordings with file paths but no text content
            cursor.execute("""
                SELECT id, raw_transcript_path, diarized_transcript_path 
                FROM recordings 
                WHERE (raw_transcript_path IS NOT NULL AND raw_transcript_text IS NULL)
                   OR (diarized_transcript_path IS NOT NULL AND diarized_transcript_text IS NULL)
            """)
            recordings_to_migrate = cursor.fetchall()
            
            migrated_count = 0
            for recording in recordings_to_migrate:
                recording_id = recording['id']
                raw_path = recording['raw_transcript_path']
                diarized_path = recording['diarized_transcript_path']
                
                # Migrate raw transcript
                if raw_path:
                    abs_path = from_project_relative_path(raw_path)
                    if os.path.exists(abs_path):
                        try:
                            with open(abs_path, 'r', encoding='utf-8') as f:
                                content = f.read()
                            cursor.execute("UPDATE recordings SET raw_transcript_text = ?, raw_transcript_path = NULL WHERE id = ?", 
                                         (content, recording_id))
                            os.remove(abs_path)
                            logger.info(f"Migrated raw transcript for recording {recording_id}")
                            migrated_count += 1
                        except Exception as e:
                            logger.error(f"Failed to migrate raw transcript for recording {recording_id}: {e}")
                
                # Migrate diarized transcript
                if diarized_path:
                    abs_path = from_project_relative_path(diarized_path)
                    if os.path.exists(abs_path):
                        try:
                            with open(abs_path, 'r', encoding='utf-8') as f:
                                content = f.read()
                            cursor.execute("UPDATE recordings SET diarized_transcript_text = ?, diarized_transcript_path = NULL WHERE id = ?", 
                                         (content, recording_id))
                            os.remove(abs_path)
                            logger.info(f"Migrated diarized transcript for recording {recording_id}")
                            migrated_count += 1
                        except Exception as e:
                            logger.error(f"Failed to migrate diarized transcript for recording {recording_id}: {e}")
            
            conn.commit()
            if migrated_count > 0:
                logger.info(f"Successfully migrated {migrated_count} transcript files to database")
            else:
                logger.debug("No transcript files found to migrate")
                
    except Exception as e:
        logger.error(f"Error during transcript migration: {e}", exc_info=True)