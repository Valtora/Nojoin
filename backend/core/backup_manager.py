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
                    zipf.write(config_path, "config.json")

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
    async def restore_backup(zip_path: str, clear_existing: bool = False):
        path_manager = PathManager()
        recordings_dir = path_manager.recordings_directory
        config_path = path_manager.config_path
        
        # ID Mapping for additive restore
        # Map: table_name -> { old_id: new_id }
        id_map: Dict[str, Dict[int, int]] = {name: {} for name, _ in MODELS}

        with zipfile.ZipFile(zip_path, 'r') as zipf:
            # Check version compatibility
            if "backup_info.json" in zipf.namelist():
                try:
                    info = json.loads(zipf.read("backup_info.json"))
                    backup_version = info.get("version", "0.0.0")
                    current_version = BackupManager._get_app_version()
                    
                    if backup_version != current_version:
                        logger.info(f"Restoring backup from version {backup_version} to {current_version}")
                        # Simple lexicographical check for now as a heuristic
                        if backup_version > current_version:
                            logger.warning(f"WARNING: Restoring backup from NEWER version ({backup_version}) to OLDER version ({current_version}). This may cause issues.")
                except Exception as e:
                    logger.warning(f"Failed to read backup info: {e}")

            # 1. Clear Existing Data if requested
            if clear_existing:
                # Clear DB
                async with async_session_maker() as session:
                    # Delete in reverse order
                    for table_name, model_cls in reversed(MODELS):
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
                    # If additive, we probably want to extract. If file exists, overwrite?
                    # Standard unzip overwrites.
                    zipf.extract(file, path_manager.user_data_directory)
                elif file == "config.json":
                    if clear_existing:
                        zipf.extract(file, path_manager.user_data_directory)

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
                        
                        # Handle Additive Logic (Remap IDs)
                        if not clear_existing:
                            # Remove ID to let DB generate new one
                            if "id" in item_data:
                                del item_data["id"]
                            
                            # Remap FKs
                            if table_name == "global_speakers":
                                if item_data.get("user_id") in id_map["users"]:
                                    item_data["user_id"] = id_map["users"][item_data["user_id"]]
                            
                            elif table_name == "tags":
                                if item_data.get("user_id") in id_map["users"]:
                                    item_data["user_id"] = id_map["users"][item_data["user_id"]]

                            elif table_name == "recordings":
                                if item_data.get("user_id") in id_map["users"]:
                                    item_data["user_id"] = id_map["users"][item_data["user_id"]]

                            elif table_name == "recording_speakers":
                                if item_data.get("recording_id") in id_map["recordings"]:
                                    item_data["recording_id"] = id_map["recordings"][item_data["recording_id"]]
                                if item_data.get("global_speaker_id") in id_map["global_speakers"]:
                                    item_data["global_speaker_id"] = id_map["global_speakers"][item_data["global_speaker_id"]]
                            
                            elif table_name == "recording_tags":
                                if item_data.get("recording_id") in id_map["recordings"]:
                                    item_data["recording_id"] = id_map["recordings"][item_data["recording_id"]]
                                if item_data.get("tag_id") in id_map["tags"]:
                                    item_data["tag_id"] = id_map["tags"][item_data["tag_id"]]

                            elif table_name == "transcripts":
                                if item_data.get("recording_id") in id_map["recordings"]:
                                    item_data["recording_id"] = id_map["recordings"][item_data["recording_id"]]

                            elif table_name == "chat_messages":
                                if item_data.get("recording_id") in id_map["recordings"]:
                                    item_data["recording_id"] = id_map["recordings"][item_data["recording_id"]]
                                if item_data.get("user_id") in id_map["users"]:
                                    item_data["user_id"] = id_map["users"][item_data["user_id"]]
                        
                        # Create instance
                        instance = model_cls(**item_data)
                        session.add(instance)
                        await session.flush() # To get the new ID
                        await session.refresh(instance)
                        
                        if not clear_existing and old_id is not None:
                            id_map[table_name][old_id] = instance.id
                
                await session.commit()
