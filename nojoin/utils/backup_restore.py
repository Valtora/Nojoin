"""
Backup and Restore functionality for Nojoin application.

This module provides comprehensive backup and restore capabilities that include:
- Complete database backup with all transcripts and meeting notes
- Audio file backup 
- Non-destructive restore that merges with existing data
- Progress tracking for large operations
"""

import os
import json
import shutil
import tempfile
import zipfile
import logging
from datetime import datetime
from typing import Dict, List, Optional, Callable
from pathlib import Path

from ..db import database as db_ops
from ..utils.config_manager import get_project_root, from_project_relative_path, to_project_relative_path

logger = logging.getLogger(__name__)


class BackupRestoreManager:
    """Manages backup and restore operations for the Nojoin application."""
    
    def __init__(self):
        self.project_root = get_project_root()
        
    def create_backup(self, backup_path: str, include_audio: bool = False, progress_callback: Optional[Callable[[int, str], None]] = None) -> bool:
        """
        Creates a backup of the application data.
        
        Args:
            backup_path: Path where the backup zip file will be created
            include_audio: Whether to include audio files in the backup (default: False)
            progress_callback: Optional callback function for progress updates (percentage, message)
            
        Returns:
            True if backup was successful, False otherwise
        """
        try:
            if progress_callback:
                progress_callback(0, "Starting backup...")
                
            # Ensure migrations are up to date
            db_ops.run_migrations()
            
            with tempfile.TemporaryDirectory() as temp_dir:
                temp_backup_dir = os.path.join(temp_dir, "nojoin_backup")
                os.makedirs(temp_backup_dir, exist_ok=True)
                
                if progress_callback:
                    progress_callback(10, "Copying database...")
                
                # 1. Copy database file
                db_path = db_ops.DB_PATH
                if os.path.exists(db_path):
                    shutil.copy2(db_path, os.path.join(temp_backup_dir, "nojoin_data.db"))
                else:
                    logger.error(f"Database file not found at {db_path}")
                    return False
                
                copied_files = []
                total_recordings = 0
                
                # 2. Copy audio files (if requested)
                if include_audio:
                    if progress_callback:
                        progress_callback(30, "Gathering audio files...")
                    
                    recordings = db_ops.get_all_recordings()
                    audio_dir = os.path.join(temp_backup_dir, "audio")
                    os.makedirs(audio_dir, exist_ok=True)
                    
                    total_recordings = len(recordings)
                    
                    for i, recording in enumerate(recordings):
                        if progress_callback:
                            progress = 30 + int((i / total_recordings) * 50)
                            progress_callback(progress, f"Copying audio file {i+1}/{total_recordings}")
                        
                        recording_dict = dict(recording)
                        audio_path = recording_dict.get('audio_path')
                        
                        if audio_path:
                            abs_audio_path = from_project_relative_path(audio_path)
                            if os.path.exists(abs_audio_path):
                                try:
                                    # Preserve relative path structure in backup
                                    backup_audio_path = os.path.join(audio_dir, os.path.basename(abs_audio_path))
                                    shutil.copy2(abs_audio_path, backup_audio_path)
                                    copied_files.append({
                                        'recording_id': recording_dict['id'],
                                        'original_path': audio_path,
                                        'backup_filename': os.path.basename(abs_audio_path)
                                    })
                                except Exception as e:
                                    logger.error(f"Failed to copy audio file {abs_audio_path}: {e}")
                            else:
                                logger.warning(f"Audio file not found: {abs_audio_path}")
                else:
                    if progress_callback:
                        progress_callback(30, "Skipping audio files...")
                    # Still get recording count for manifest
                    recordings = db_ops.get_all_recordings()
                    total_recordings = len(recordings)
                
                if progress_callback:
                    progress_callback(85, "Creating manifest...")
                
                # 3. Create manifest
                manifest = {
                    'version': '1.0',
                    'created_at': datetime.now().isoformat(),
                    'app_version': 'nojoin-1.0',  # Could be dynamic
                    'total_recordings': total_recordings,
                    'copied_files': copied_files,
                    'includes_audio': include_audio,
                    'backup_type': 'complete' if include_audio else 'database_only'
                }
                
                manifest_path = os.path.join(temp_backup_dir, "manifest.json")
                with open(manifest_path, 'w', encoding='utf-8') as f:
                    json.dump(manifest, f, indent=2)
                
                if progress_callback:
                    progress_callback(90, "Creating zip archive...")
                
                # 4. Create zip file
                with zipfile.ZipFile(backup_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                    for root, dirs, files in os.walk(temp_backup_dir):
                        for file in files:
                            file_path = os.path.join(root, file)
                            arc_path = os.path.relpath(file_path, temp_backup_dir)
                            zipf.write(file_path, arc_path)
                
                if progress_callback:
                    progress_callback(100, "Backup complete!")
                
                logger.info(f"Backup created successfully at {backup_path}")
                return True
                
        except Exception as e:
            logger.error(f"Error creating backup: {e}", exc_info=True)
            if progress_callback:
                progress_callback(-1, f"Backup failed: {str(e)}")
            return False
    
    def restore_backup(self, backup_path: str, progress_callback: Optional[Callable[[int, str], None]] = None) -> bool:
        """
        Restores data from a backup file in a non-destructive way.
        
        Args:
            backup_path: Path to the backup zip file
            progress_callback: Optional callback function for progress updates (percentage, message)
            
        Returns:
            True if restore was successful, False otherwise
        """
        try:
            if not os.path.exists(backup_path):
                logger.error(f"Backup file not found: {backup_path}")
                return False
                
            if progress_callback:
                progress_callback(0, "Starting restore...")
            
            with tempfile.TemporaryDirectory() as temp_dir:
                restore_dir = os.path.join(temp_dir, "restore")
                
                if progress_callback:
                    progress_callback(10, "Extracting backup...")
                
                # 1. Extract backup
                with zipfile.ZipFile(backup_path, 'r') as zipf:
                    zipf.extractall(restore_dir)
                
                # 2. Read manifest
                manifest_path = os.path.join(restore_dir, "manifest.json")
                if not os.path.exists(manifest_path):
                    logger.error("Invalid backup: manifest.json not found")
                    return False
                
                with open(manifest_path, 'r', encoding='utf-8') as f:
                    manifest = json.load(f)
                
                if progress_callback:
                    progress_callback(25, "Reading backup manifest...")
                
                logger.info(f"Restoring backup created at {manifest.get('created_at')}")
                
                # 3. Restore database (merge with existing)
                backup_db_path = os.path.join(restore_dir, "nojoin_data.db")
                if os.path.exists(backup_db_path):
                    if progress_callback:
                        progress_callback(40, "Merging database records...")
                    
                    success = self._merge_database_records(backup_db_path)
                    if not success:
                        logger.error("Failed to merge database records")
                        return False
                else:
                    logger.error("Database file not found in backup")
                    return False
                
                # 4. Restore audio files (if included in backup)
                audio_backup_dir = os.path.join(restore_dir, "audio")
                copied_files = manifest.get('copied_files', [])
                includes_audio = manifest.get('includes_audio', True)  # Default to True for older backups
                
                if includes_audio and os.path.exists(audio_backup_dir) and copied_files:
                    if progress_callback:
                        progress_callback(60, "Restoring audio files...")
                    
                    self._restore_audio_files(audio_backup_dir, copied_files, progress_callback)
                else:
                    if progress_callback:
                        if not includes_audio:
                            progress_callback(60, "No audio files in backup to restore...")
                        else:
                            progress_callback(60, "No audio files found in backup...")
                
                if progress_callback:
                    progress_callback(100, "Restore complete!")
                
                logger.info("Backup restore completed successfully")
                return True
                
        except Exception as e:
            logger.error(f"Error restoring backup: {e}", exc_info=True)
            if progress_callback:
                progress_callback(-1, f"Restore failed: {str(e)}")
            return False
    
    def _merge_database_records(self, backup_db_path: str) -> bool:
        """
        Merge records from backup database into current database.
        Uses INSERT OR IGNORE to avoid conflicts with existing records.
        Handles foreign key constraints by inserting in the correct order.
        """
        try:
            import sqlite3
            
            # Use a separate connection for the backup database to ensure proper cleanup
            backup_conn = None
            try:
                # Open backup database separately
                backup_conn = sqlite3.connect(backup_db_path)
                backup_conn.row_factory = sqlite3.Row
                
                # Open current database
                with db_ops.get_db_connection() as current_conn:
                    # Start a transaction for the entire merge operation
                    current_conn.execute("BEGIN TRANSACTION")
                    
                    try:
                        # Temporarily disable foreign key constraints during merge
                        current_conn.execute("PRAGMA foreign_keys = OFF")
                        
                        # Insert records in correct order to satisfy foreign key constraints
                        
                        # 1. First insert parent tables (no foreign key dependencies)
                        # Insert recordings first
                        try:
                            cursor = backup_conn.cursor()
                            cursor.execute("SELECT * FROM recordings")
                            recordings = cursor.fetchall()
                            for row in recordings:
                                record = dict(row)  # Convert Row to dict for .get() method
                                current_conn.execute("""
                                    INSERT OR IGNORE INTO recordings 
                                    (id, name, created_at, processed_at, start_time, end_time, audio_path, 
                                     raw_transcript_path, diarized_transcript_path, raw_transcript_text, 
                                     diarized_transcript_text, tags, format, duration_seconds, 
                                     file_size_bytes, status, chat_history)
                                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                                """, (
                                    record['id'], record['name'], record['created_at'], record['processed_at'],
                                    record['start_time'], record['end_time'], record['audio_path'],
                                    record.get('raw_transcript_path'), record.get('diarized_transcript_path'),
                                    record.get('raw_transcript_text'), record.get('diarized_transcript_text'),
                                    record.get('tags'), record.get('format', 'MP3'), record.get('duration_seconds'),
                                    record.get('file_size_bytes'), record.get('status', 'Recorded'),
                                    record.get('chat_history')
                                ))
                            logger.info(f"Merged {len(recordings)} recordings")
                        except sqlite3.OperationalError as e:
                            logger.warning(f"Error merging recordings (table may not exist in backup): {e}")
                        
                        # Insert global_speakers
                        try:
                            cursor = backup_conn.cursor()
                            cursor.execute("SELECT * FROM global_speakers")
                            global_speakers = cursor.fetchall()
                            for row in global_speakers:
                                record = dict(row)  # Convert Row to dict
                                current_conn.execute("""
                                    INSERT OR IGNORE INTO global_speakers (id, name)
                                    VALUES (?, ?)
                                """, (record['id'], record['name']))
                            logger.info(f"Merged {len(global_speakers)} global speakers")
                        except sqlite3.OperationalError as e:
                            logger.warning(f"Error merging global speakers (table may not exist in backup): {e}")
                        
                        # Insert tags
                        try:
                            cursor = backup_conn.cursor()
                            cursor.execute("SELECT * FROM tags")
                            tags = cursor.fetchall()
                            for row in tags:
                                record = dict(row)  # Convert Row to dict
                                current_conn.execute("""
                                    INSERT OR IGNORE INTO tags (id, name)
                                    VALUES (?, ?)
                                """, (record['id'], record['name']))
                            logger.info(f"Merged {len(tags)} tags")
                        except sqlite3.OperationalError as e:
                            logger.warning(f"Error merging tags (table may not exist in backup): {e}")
                        
                        # 2. Then insert child tables (with foreign key dependencies)
                        # Insert speakers (depends on global_speakers)
                        try:
                            cursor = backup_conn.cursor()
                            cursor.execute("SELECT * FROM speakers")
                            speakers = cursor.fetchall()
                            for row in speakers:
                                record = dict(row)  # Convert Row to dict
                                current_conn.execute("""
                                    INSERT OR IGNORE INTO speakers (id, name, voice_snippet_path, global_speaker_id)
                                    VALUES (?, ?, ?, ?)
                                """, (record['id'], record['name'], record.get('voice_snippet_path'), record.get('global_speaker_id')))
                            logger.info(f"Merged {len(speakers)} speakers")
                        except sqlite3.OperationalError as e:
                            logger.warning(f"Error merging speakers (table may not exist in backup): {e}")
                        
                        # Insert recording_speakers (depends on recordings and speakers)
                        try:
                            cursor = backup_conn.cursor()
                            cursor.execute("SELECT * FROM recording_speakers")
                            recording_speakers = cursor.fetchall()
                            for row in recording_speakers:
                                record = dict(row)  # Convert Row to dict
                                current_conn.execute("""
                                    INSERT OR IGNORE INTO recording_speakers 
                                    (recording_id, speaker_id, diarization_label, snippet_start, snippet_end)
                                    VALUES (?, ?, ?, ?, ?)
                                """, (record['recording_id'], record['speaker_id'], record['diarization_label'],
                                      record.get('snippet_start'), record.get('snippet_end')))
                            logger.info(f"Merged {len(recording_speakers)} recording-speaker relationships")
                        except sqlite3.OperationalError as e:
                            logger.warning(f"Error merging recording_speakers (table may not exist in backup): {e}")
                        
                        # Insert recording_tags (depends on recordings and tags)
                        try:
                            cursor = backup_conn.cursor()
                            cursor.execute("SELECT * FROM recording_tags")
                            recording_tags = cursor.fetchall()
                            for row in recording_tags:
                                record = dict(row)  # Convert Row to dict
                                current_conn.execute("""
                                    INSERT OR IGNORE INTO recording_tags (recording_id, tag_id)
                                    VALUES (?, ?)
                                """, (record['recording_id'], record['tag_id']))
                            logger.info(f"Merged {len(recording_tags)} recording-tag relationships")
                        except sqlite3.OperationalError as e:
                            logger.warning(f"Error merging recording_tags (table may not exist in backup): {e}")
                        
                        # Insert meeting_notes (depends on recordings)
                        try:
                            cursor = backup_conn.cursor()
                            cursor.execute("SELECT * FROM meeting_notes")
                            meeting_notes = cursor.fetchall()
                            for row in meeting_notes:
                                record = dict(row)  # Convert Row to dict
                                current_conn.execute("""
                                    INSERT OR IGNORE INTO meeting_notes 
                                    (id, recording_id, llm_backend, model, notes, created_at, updated_at)
                                    VALUES (?, ?, ?, ?, ?, ?, ?)
                                """, (record['id'], record['recording_id'], record['llm_backend'],
                                      record['model'], record['notes'], record['created_at'], record.get('updated_at')))
                            logger.info(f"Merged {len(meeting_notes)} meeting notes")
                        except sqlite3.OperationalError as e:
                            logger.warning(f"Error merging meeting_notes (table may not exist in backup): {e}")
                        
                        # Re-enable foreign key constraints
                        current_conn.execute("PRAGMA foreign_keys = ON")
                        
                        # Commit the transaction
                        current_conn.execute("COMMIT")
                        
                    except Exception as e:
                        # Rollback on any error during the merge
                        current_conn.execute("ROLLBACK")
                        current_conn.execute("PRAGMA foreign_keys = ON")  # Re-enable constraints
                        raise e
                
                logger.info("Database records merged successfully")
                return True
                
            finally:
                # Ensure backup connection is properly closed
                if backup_conn:
                    backup_conn.close()
                    logger.debug("Backup database connection closed")
                
        except Exception as e:
            logger.error(f"Error merging database records: {e}", exc_info=True)
            return False
    
    def _restore_audio_files(self, audio_backup_dir: str, copied_files: List[Dict], progress_callback: Optional[Callable[[int, str], None]] = None):
        """Restore audio files, skipping any that already exist."""
        total_files = len(copied_files)
        
        for i, file_info in enumerate(copied_files):
            if progress_callback:
                progress = 60 + int((i / total_files) * 30)
                progress_callback(progress, f"Restoring audio {i+1}/{total_files}")
            
            backup_filename = file_info['backup_filename']
            original_path = file_info['original_path']
            
            backup_file_path = os.path.join(audio_backup_dir, backup_filename)
            if not os.path.exists(backup_file_path):
                logger.warning(f"Backup file not found: {backup_file_path}")
                continue
            
            # Determine target path
            target_abs_path = from_project_relative_path(original_path)
            
            # Only copy if target doesn't exist (non-destructive)
            if not os.path.exists(target_abs_path):
                try:
                    # Ensure target directory exists
                    os.makedirs(os.path.dirname(target_abs_path), exist_ok=True)
                    shutil.copy2(backup_file_path, target_abs_path)
                    logger.info(f"Restored audio file: {target_abs_path}")
                except Exception as e:
                    logger.error(f"Failed to restore audio file {backup_filename}: {e}")
            else:
                logger.debug(f"Audio file already exists, skipping: {target_abs_path}")


def get_default_backup_filename() -> str:
    """Generate a default backup filename with timestamp."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"nojoin_backup_{timestamp}.zip" 