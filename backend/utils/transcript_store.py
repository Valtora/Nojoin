"""
TranscriptStore - Abstraction layer for transcript storage in database.

This module provides a clean interface for reading and writing transcript content
to the database, replacing file-based operations while maintaining compatibility
with existing code.
"""

import logging
from typing import Optional, Callable

logger = logging.getLogger(__name__)


class TranscriptStore:
    """Provides database-backed transcript storage with a simple interface."""
    
    @staticmethod
    def get(recording_id: str, kind: str = "diarized") -> Optional[str]:
        """
        Retrieve transcript text from database.
        
        Args:
            recording_id: The recording ID
            kind: Either "diarized" or "raw"
            
        Returns:
            The transcript text or None if not found
        """
        if kind not in ["diarized", "raw"]:
            logger.error(f"Invalid transcript kind: {kind}. Must be 'diarized' or 'raw'")
            return None
            
        recording_id = str(recording_id)
        try:
            with db_ops.get_db_connection() as conn:
                cursor = conn.cursor()
                column = f"{kind}_transcript_text"
                cursor.execute(f"SELECT {column} FROM recordings WHERE id = ?", (recording_id,))
                row = cursor.fetchone()
                if row and row[0] is not None:
                    return row[0]
                else:
                    logger.warning(f"No {kind} transcript text found for recording {recording_id}")
                    return None
        except Exception as e:
            logger.error(f"Error retrieving {kind} transcript for recording {recording_id}: {e}", exc_info=True)
            return None
    
    @staticmethod
    def set(recording_id: str, text: str, kind: str = "diarized") -> bool:
        """
        Store transcript text in database.
        
        Args:
            recording_id: The recording ID
            text: The transcript content
            kind: Either "diarized" or "raw"
            
        Returns:
            True if successful, False otherwise
        """
        if kind not in ["diarized", "raw"]:
            logger.error(f"Invalid transcript kind: {kind}. Must be 'diarized' or 'raw'")
            return False
            
        recording_id = str(recording_id)
        try:
            with db_ops.get_db_connection() as conn:
                cursor = conn.cursor()
                column = f"{kind}_transcript_text"
                cursor.execute(f"UPDATE recordings SET {column} = ? WHERE id = ?", (text, recording_id))
                conn.commit()
                if cursor.rowcount == 0:
                    logger.warning(f"No recording found with ID {recording_id} to update {kind} transcript")
                    return False
                logger.info(f"Successfully stored {kind} transcript for recording {recording_id}")
                return True
        except Exception as e:
            logger.error(f"Error storing {kind} transcript for recording {recording_id}: {e}", exc_info=True)
            return False
    
    @staticmethod
    def replace(recording_id: str, replacement_fn: Callable[[str], tuple[str, int]], kind: str = "diarized") -> int:
        """
        Apply a replacement function to transcript text and store the result.
        
        Args:
            recording_id: The recording ID
            replacement_fn: Function that takes text and returns (new_text, replacement_count)
            kind: Either "diarized" or "raw"
            
        Returns:
            Number of replacements made, or -1 on error
        """
        current_text = TranscriptStore.get(recording_id, kind)
        if current_text is None:
            logger.warning(f"No {kind} transcript found for recording {recording_id} to perform replacement")
            return -1
            
        try:
            new_text, replacement_count = replacement_fn(current_text)
            if replacement_count > 0:
                if TranscriptStore.set(recording_id, new_text, kind):
                    logger.info(f"Made {replacement_count} replacements in {kind} transcript for recording {recording_id}")
                    return replacement_count
                else:
                    logger.error(f"Failed to store updated {kind} transcript for recording {recording_id}")
                    return -1
            else:
                logger.debug(f"No replacements needed in {kind} transcript for recording {recording_id}")
                return 0
        except Exception as e:
            logger.error(f"Error applying replacement function to {kind} transcript for recording {recording_id}: {e}", exc_info=True)
            return -1
    
    @staticmethod
    def exists(recording_id: str, kind: str = "diarized") -> bool:
        """
        Check if transcript text exists for the given recording.
        
        Args:
            recording_id: The recording ID
            kind: Either "diarized" or "raw"
            
        Returns:
            True if transcript exists and is not empty, False otherwise
        """
        text = TranscriptStore.get(recording_id, kind)
        return text is not None and text.strip() != "" 