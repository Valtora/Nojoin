import zipfile
import json
import os
import shutil
import tempfile
import subprocess
import logging
from datetime import datetime, timezone
from typing import List, Dict, Any, Type, Tuple
from sqlmodel import select, SQLModel, delete
from sqlalchemy import text
from backend.core.db import async_session_maker
from backend.models.user import User
from backend.models.speaker import GlobalSpeaker, RecordingSpeaker
from backend.models.recording import Recording
from backend.models.tag import Tag, RecordingTag
from backend.models.transcript import Transcript
from backend.models.chat import ChatMessage
from backend.utils.path_manager import PathManager
from backend.utils.audio import ensure_ffmpeg_in_path

logger = logging.getLogger(__name__)

# Order matters for restoration
MODELS: List[Tuple[str, Type[SQLModel]]] = [
    ("users", User),
    ("global_speakers", GlobalSpeaker),
    ("tags", Tag),
    ("recordings", Recording),
    ("recording_speakers", RecordingSpeaker),
    ("recording_tags", RecordingTag),
    ("transcripts", Transcript),
    ("chat_messages", ChatMessage)
]

class BackupManager:
    @staticmethod
    def _get_app_version() -> str:
        try:
            path_manager = PathManager()
            # Try to find VERSION file in docs folder relative to executable/project root
            version_path = path_manager.executable_directory / "docs" / "VERSION"
            if version_path.exists():
                return version_path.read_text().strip()
            return "0.0.0"
        except Exception:
            return "0.0.0"

    @staticmethod
    def _redact_sensitive_data(data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Recursively redact sensitive keys from a dictionary.
        """
        redacted = data.copy()
        for k, v in redacted.items():
            if isinstance(v, dict):
                redacted[k] = BackupManager._redact_sensitive_data(v)
            elif isinstance(k, str) and (k.endswith("_key") or k.endswith("_token") or "password" in k):
                if v: # Only redact if there is a value
                    redacted[k] = "REDACTED"
        return redacted

    @staticmethod
    def _adapt_record(model_cls: Type[SQLModel], data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Adapts a record dictionary to match the current model schema.
        Removes fields that no longer exist in the model.
        """
        # Get current field names
        # SQLModel uses model_fields in Pydantic v2, __fields__ in v1
        if hasattr(model_cls, "model_fields"):
            current_fields = model_cls.model_fields.keys()
        else:
            current_fields = model_cls.__fields__.keys()
            
        # Filter data to only include fields that exist in the current model
        return {k: v for k, v in data.items() if k in current_fields}

    @staticmethod
    def _compress_to_opus(input_path: str) -> str:
        """
        Compresses audio file to Opus format in a temporary file.
        Returns path to temporary opus file.
        """
        ensure_ffmpeg_in_path()
        temp_opus = tempfile.NamedTemporaryFile(delete=False, suffix=".opus")
        temp_opus.close()
        
        cmd = [
            "ffmpeg",
            "-y",
            "-i", input_path,
            "-c:a", "libopus",
            "-b:a", "64k", # 64k is good for speech
            "-v", "error",
            temp_opus.name
        ]
        
        try:
            subprocess.run(cmd, check=True, capture_output=True)
            return temp_opus.name
        except subprocess.CalledProcessError as e:
            if os.path.exists(temp_opus.name):
                os.remove(temp_opus.name)
            raise RuntimeError(f"FFmpeg compression failed: {e}")

    @staticmethod
    async def create_backup() -> str:
        path_manager = PathManager()
        recordings_dir = path_manager.recordings_directory
        config_path = path_manager.config_path

        # Create a temporary file for the zip
        temp_zip = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
        temp_zip.close()

        try:
            with zipfile.ZipFile(temp_zip.name, 'w', zipfile.ZIP_DEFLATED) as zipf:
                # 1. Dump Database
                async with async_session_maker() as session:
                    for table_name, model_cls in MODELS:
                        statement = select(model_cls)
                        results = await session.execute(statement)
                        items = results.scalars().all()
                        
                        # Serialize
                        data = [item.model_dump(mode='json') for item in items]
                        
                        # Modify recording paths to point to .opus files
                        if table_name == "recordings":
                            for item in data:
                                if "audio_path" in item and item["audio_path"]:
                                    # Check if it's an audio file we intend to compress
                                    ext = os.path.splitext(item["audio_path"])[1].lower()
                                    if ext in ['.wav', '.mp3', '.m4a', '.ogg', '.flac']:
                                        base, _ = os.path.splitext(item["audio_path"])
                                        item["audio_path"] = base + ".opus"
                                        # Reset file size as it will change
                                        item["file_size_bytes"] = None
                                        
                        # Redact sensitive data from Users
                        if table_name == "users":
                            for item in data:
                                if "settings" in item and item["settings"]:
                                    # Redact API keys and tokens
                                    item["settings"] = BackupManager._redact_sensitive_data(item["settings"])

                        zipf.writestr(f"{table_name}.json", json.dumps(data, indent=2))

                # 2. Add Recordings
                if recordings_dir.exists():
                    for root, dirs, files in os.walk(recordings_dir):
                        for file in files:
                            file_path = os.path.join(root, file)
                            
                            # Check if audio file to compress
                            ext = os.path.splitext(file)[1].lower()
                            if ext in ['.wav', '.mp3', '.m4a', '.ogg', '.flac']:
                                try:
                                    opus_path = BackupManager._compress_to_opus(file_path)
                                    
                                    # Calculate arcname with .opus extension
                                    rel_path = os.path.relpath(file_path, recordings_dir)
                                    rel_path_base, _ = os.path.splitext(rel_path)
                                    arcname = os.path.join("recordings", rel_path_base + ".opus")
                                    
                                    zipf.write(opus_path, arcname)
                                    os.remove(opus_path)
                                except Exception as e:
                                    # If compression fails, we have a problem because DB dump expects .opus
                                    # But we can't easily revert the DB dump in the zip.
                                    # We should probably fail the backup.
                                    raise RuntimeError(f"Failed to compress {file}: {e}")
                            else:
                                # Non-audio files or already opus
                                arcname = os.path.join("recordings", os.path.relpath(file_path, recordings_dir))
                                zipf.write(file_path, arcname)

                # 3. Add Config
                if config_path.exists():
                    try:
                        config_data = json.loads(config_path.read_text())
                        # Redact sensitive config
                        config_data = BackupManager._redact_sensitive_data(config_data)
                        zipf.writestr("config.json", json.dumps(config_data, indent=2))
                    except Exception as e:
                        logger.error(f"Failed to back up config: {e}")

                # 4. Add Backup Info
                backup_info = {
                    "version": BackupManager._get_app_version(),
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }
                zipf.writestr("backup_info.json", json.dumps(backup_info, indent=2))

            return temp_zip.name
            
        except Exception as e:
            # Cleanup temp zip if failed
            if os.path.exists(temp_zip.name):
                os.remove(temp_zip.name)
            raise e

    @staticmethod
    async def restore_backup(zip_path: str, clear_existing: bool = False, overwrite_existing: bool = False):
        path_manager = PathManager()
        recordings_dir = path_manager.recordings_directory
        config_path = path_manager.config_path
        
        # ID Mapping for additive restore
        # Map: table_name -> { old_id: new_id }
        id_map: Dict[str, Dict[int, int]] = {name: {} for name, _ in MODELS}
        
        # Map: audio_path -> new_id (Track processed paths to avoid self-deletion of duplicates in backup)
        restored_audio_paths: Dict[str, int] = {}

        # Set of skipped recording IDs (old_id) - used to skip children
        skipped_recording_ids = set()

        with zipfile.ZipFile(zip_path, 'r') as zipf:
            # Check version compatibility
            if "backup_info.json" in zipf.namelist():
                try:
                    info = json.loads(zipf.read("backup_info.json"))
                    backup_version = info.get("version", "0.0.0")
                    current_version = BackupManager._get_app_version()
                    
                    if backup_version != current_version:
                        logger.info(f"Restoring backup from version {backup_version} to {current_version}")
                        if backup_version > current_version:
                            logger.warning(f"WARNING: Restoring backup from NEWER version ({backup_version}) to OLDER version ({current_version}). This may cause issues.")
                except Exception as e:
                    logger.warning(f"Failed to read backup info: {e}")

            # 1. Clear Existing Data if requested
            if clear_existing:
                # Clear DB, BUT SKIP USERS to prevent lockout
                async with async_session_maker() as session:
                    # Delete in reverse order
                    for table_name, model_cls in reversed(MODELS):
                        if table_name == "users":
                            continue
                        await session.execute(delete(model_cls))
                    await session.commit()
                
                # Clear Recordings
                if recordings_dir.exists():
                    shutil.rmtree(recordings_dir)
                recordings_dir.mkdir(parents=True, exist_ok=True)

            # 2. Restore Files
            # Extract recordings
            for file in zipf.namelist():
                if file.startswith("recordings/"):
                    # We need to be careful not to overwrite if not clear_existing?
                    # If overwrite_existing is True, standard unzip overwrites, which is what we want.
                    # If overwrite_existing is False, we should ideally NOT extract if it exists.
                    # HOWEVER, checking existence for every file might be slow.
                    # AND the file path might be different from what's in the DB if we handle collisions?
                    # But here the filenames are hashed/timestamped usually.
                    
                    # Optimization: Just extract all. If we skip the DB record, the file is orphaned but harmless.
                    zipf.extract(file, path_manager.user_data_directory)
                elif file == "config.json":
                    if clear_existing:
                        # Only restore config if we are clearing data, otherwise keep current system config
                        try:
                            new_config = json.loads(zipf.read("config.json"))
                            if config_path.exists():
                                current_config = json.loads(config_path.read_text())
                                # Merge: Update current with new, but keep current if new is REDACTED
                                for k, v in new_config.items():
                                    if v != "REDACTED":
                                        current_config[k] = v
                                config_path.write_text(json.dumps(current_config, indent=2))
                            else:
                                zipf.extract(file, path_manager.user_data_directory)
                        except Exception as e:
                            logger.error(f"Failed to restore config: {e}")

            # 2a. Pre-flight Cleanup (Overwrite Strategy)
            if overwrite_existing and not clear_existing:
                if "recordings.json" in zipf.namelist():
                    try:
                        rec_data = json.loads(zipf.read("recordings.json"))
                        audio_paths = [item.get("audio_path") for item in rec_data if item.get("audio_path")]
                        
                        if audio_paths:
                            async with async_session_maker() as session:
                                # We need to handle this in chunks if too many?
                                # For now, simple IN clause.
                                # Find existing IDs to log
                                existing_stm = select(Recording.id).where(Recording.audio_path.in_(audio_paths))
                                existing_ids = (await session.execute(existing_stm)).scalars().all()
                                
                                if existing_ids:
                                    logger.info(f"Overwrite: Pre-deleting {len(existing_ids)} conflicting recordings.")
                                    # specific delete to trigger cascades if needed (though delete from recordings usually cascades)
                                    await session.execute(delete(Recording).where(Recording.id.in_(existing_ids)))
                                    await session.commit()
                    except Exception as e:
                        logger.error(f"Pre-flight cleanup failed: {e}")

            # 3. Restore Database
            async with async_session_maker() as session:
                for table_name, model_cls in MODELS:
                    if f"{table_name}.json" not in zipf.namelist():
                        continue
                    
                    data = json.loads(zipf.read(f"{table_name}.json"))
                    
                    for item_data in data:
                        # Adapt record to current schema (handle removed columns)
                        item_data = BackupManager._adapt_record(model_cls, item_data)

                        old_id = item_data.get("id")
                        
                        # Handle Conflict / Additive Logic
                        
                        # Special handling for Users: Resolve by username
                        if table_name == "users":
                            username = item_data.get("username")
                            existing_user = (await session.execute(select(User).where(User.username == username))).scalar_one_or_none()
                            
                            if existing_user:
                                # User exists. Map old_id to existing_id.
                                # Do NOT overwrite the user (security risk, plus passwords etc).
                                if old_id is not None:
                                    id_map["users"][old_id] = existing_user.id
                                continue # Skip inserting this user
                            
                        # Special handling for Recordings: Resolve by audio_path
                        elif table_name == "recordings":
                            audio_path = item_data.get("audio_path")
                            
                            # DUPLICATE IN BACKUP CHECK:
                            # If we already restored this path in this session, map to that ID and SKIP.
                            if audio_path in restored_audio_paths:
                                logger.warning(f"Duplicate audio path in backup JSON: {audio_path}. Linking old_id {old_id} to existing new_id {restored_audio_paths[audio_path]}")
                                if old_id is not None:
                                    id_map["recordings"][old_id] = restored_audio_paths[audio_path]
                                continue # Skip insert

                            existing_rec = (await session.execute(select(Recording).where(Recording.audio_path == audio_path))).scalar_one_or_none()
                            
                            if existing_rec:
                                # If we are here, it means we found a conflict.
                                # If overwrite_existing was True, we SHOULD have deleted it in pre-flight.
                                # But maybe race condition or something? 
                                # Or maybe clear_existing logic?
                                
                                if overwrite_existing:
                                    # Fallback: Delete row
                                    # NOTE: Pre-flight should have caught this. If we are here, it's a straggler or race condition.
                                    logger.warning(f"Fallback delete triggered for audio_path={audio_path}. Deleting ID {existing_rec.id}.")
                                    await session.delete(existing_rec)
                                    await session.flush()
                                else:
                                    # Strategy: SKIP (Safe Merge)
                                    # Use existing ID map.
                                    if old_id is not None:
                                        id_map["recordings"][old_id] = existing_rec.id
                                        skipped_recording_ids.add(old_id)
                                        # Also track it as restored/processed so subsequent dupes map to it
                                        restored_audio_paths[audio_path] = existing_rec.id
                                    continue # Skip inserting this recording
                        
                        else:
                            # Child Records: Skip if parent recording was skipped
                            if "recording_id" in item_data:
                                old_rec_id = item_data["recording_id"]
                                if old_rec_id in skipped_recording_ids:
                                    # Parent was skipped, so we skip child to avoid duplication/errors
                                    # NOTE: We do NOT need to check if the child exists because 
                                    # if we are skipping, it means the parent exists, and we assume 
                                    # the existing parent has its own children.
                                    # We don't want to merge children from backup into existing parent 
                                    # because that causes duplication (e.g. double transcripts).
                                    continue

                            # Tags: name + user_id is unique?
                            if table_name == "tags":
                                tag_name = item_data.get("name")
                                # Fix possible FK resolution before check
                                user_id = item_data.get("user_id")
                                if user_id in id_map["users"]:
                                    user_id = id_map["users"][user_id]
                                
                                existing_tag = (await session.execute(
                                    select(Tag).where(Tag.name == tag_name).where(Tag.user_id == user_id)
                                )).scalar_one_or_none()
                                
                                if existing_tag:
                                    if old_id is not None:
                                        id_map["tags"][old_id] = existing_tag.id
                                    continue

                        # Remap Foreign Keys for insertion
                        
                        # Remove ID to let DB generate new one (always additive in practice for safety)
                        if "id" in item_data:
                            del item_data["id"]
                        
                        # Remap FKs
                        if table_name == "global_speakers":
                            if item_data.get("user_id") in id_map["users"]:
                                item_data["user_id"] = id_map["users"][item_data["user_id"]]
                            else:
                                continue # Orphaned

                        elif table_name == "tags":
                            if item_data.get("user_id") in id_map["users"]:
                                item_data["user_id"] = id_map["users"][item_data["user_id"]]
                            else:
                                continue

                        elif table_name == "recordings":
                            if item_data.get("user_id") in id_map["users"]:
                                item_data["user_id"] = id_map["users"][item_data["user_id"]]
                            else:
                                continue

                        elif table_name == "recording_speakers":
                            old_rec_id = item_data.get("recording_id")
                            if old_rec_id in id_map["recordings"]:
                                new_rec_id = id_map["recordings"][old_rec_id]
                                
                                # SANITY CHECK: Does this recording exist?
                                sanity_rec = (await session.execute(select(Recording).where(Recording.id == new_rec_id))).scalar_one_or_none()
                                if not sanity_rec:
                                    logger.error(f"CRITICAL: Recording ID {new_rec_id} (mapped from {old_rec_id}) DOES NOT EXIST! Skipping speaker.")
                                    continue

                                item_data["recording_id"] = new_rec_id
                            else:
                                continue
                            if item_data.get("global_speaker_id") in id_map["global_speakers"]:
                                item_data["global_speaker_id"] = id_map["global_speakers"][item_data["global_speaker_id"]]
                        
                        elif table_name == "recording_tags":
                            old_rec_id = item_data.get("recording_id")
                            if old_rec_id in id_map["recordings"]:
                                new_rec_id = id_map["recordings"][old_rec_id]
                                
                                # SANITY CHECK
                                sanity_rec = (await session.execute(select(Recording).where(Recording.id == new_rec_id))).scalar_one_or_none()
                                if not sanity_rec:
                                    logger.error(f"CRITICAL: Recording ID {new_rec_id} (mapped from {old_rec_id}) DOES NOT EXIST! Skipping tag link.")
                                    continue
                                    
                                item_data["recording_id"] = new_rec_id
                            else:
                                continue
                            if item_data.get("tag_id") in id_map["tags"]:
                                item_data["tag_id"] = id_map["tags"][item_data["tag_id"]]
                            else:
                                continue
                            
                            # DUPLICATE CHECK: Does this tag link already exist?
                            # (Important if we merged two recordings that both had the same tag)
                            existing_link = (await session.execute(
                                select(RecordingTag)
                                .where(RecordingTag.recording_id == item_data["recording_id"])
                                .where(RecordingTag.tag_id == item_data["tag_id"])
                            )).scalar_one_or_none()
                            
                            if existing_link:
                                logger.info(f"Skipping duplicate recording_tag link for recording_id={new_rec_id}, tag_id={item_data['tag_id']}")
                                if old_id is not None:
                                    id_map["recording_tags"][old_id] = existing_link.id # Just in case
                                continue

                        elif table_name == "transcripts":
                            old_rec_id = item_data.get("recording_id")
                            if old_rec_id in id_map["recordings"]:
                                new_rec_id = id_map["recordings"][old_rec_id]
                                
                                # SANITY CHECK
                                sanity_rec = (await session.execute(select(Recording).where(Recording.id == new_rec_id))).scalar_one_or_none()
                                if not sanity_rec:
                                    logger.error(f"CRITICAL: Recording ID {new_rec_id} (mapped from {old_rec_id}) DOES NOT EXIST! Skipping transcript.")
                                    continue
                                
                                item_data["recording_id"] = new_rec_id
                            else:
                                continue
                                
                            # DUPLICATE CHECK: Does this recording already have a transcript?
                            # (Important if we merged two recordings that both had transcripts)
                            existing_transcript = (await session.execute(
                                select(Transcript).where(Transcript.recording_id == new_rec_id)
                            )).scalar_one_or_none()
                            
                            if existing_transcript:
                                logger.info(f"Skipping duplicate transcript for recording_id={new_rec_id} (merged from {old_rec_id}). Keeping existing transcript.")
                                if old_id is not None:
                                    id_map["transcripts"][old_id] = existing_transcript.id
                                continue

                        elif table_name == "chat_messages":
                            old_rec_id = item_data.get("recording_id")
                            if old_rec_id in id_map["recordings"]:
                                new_rec_id = id_map["recordings"][old_rec_id]
                                
                                # SANITY CHECK
                                sanity_rec = (await session.execute(select(Recording).where(Recording.id == new_rec_id))).scalar_one_or_none()
                                if not sanity_rec:
                                    logger.error(f"CRITICAL: Recording ID {new_rec_id} (mapped from {old_rec_id}) DOES NOT EXIST! Skipping chat.")
                                    continue
                                    
                                item_data["recording_id"] = new_rec_id
                            else:
                                continue
                            if item_data.get("user_id") in id_map["users"]:
                                item_data["user_id"] = id_map["users"][item_data["user_id"]]
                    
                        # Create instance
                        instance = model_cls.model_validate(item_data)
                        session.add(instance)
                        await session.flush() # To get the new ID
                        await session.refresh(instance)
                        
                        if old_id is not None:
                            id_map[table_name][old_id] = instance.id
                        
                        # Track restored audio paths for duplicate detection
                        if table_name == "recordings" and instance.audio_path:
                            restored_audio_paths[instance.audio_path] = instance.id
                
                await session.commit()
