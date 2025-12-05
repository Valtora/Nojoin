import zipfile
import json
import os
import shutil
import tempfile
from datetime import datetime
from typing import List, Dict, Any, Type, Tuple
from sqlmodel import Session, select, SQLModel, delete
from sqlalchemy import text
from backend.core.db import engine
from backend.models.user import User
from backend.models.speaker import GlobalSpeaker, RecordingSpeaker
from backend.models.recording import Recording
from backend.models.tag import Tag, RecordingTag
from backend.models.transcript import Transcript
from backend.models.chat import ChatMessage
from backend.utils.path_manager import PathManager

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
    async def create_backup() -> str:
        path_manager = PathManager()
        recordings_dir = path_manager.recordings_directory
        config_path = path_manager.config_path

        # Create a temporary file for the zip
        temp_zip = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
        temp_zip.close()

        with zipfile.ZipFile(temp_zip.name, 'w', zipfile.ZIP_DEFLATED) as zipf:
            # 1. Dump Database
            async with Session(engine) as session:
                for table_name, model_cls in MODELS:
                    statement = select(model_cls)
                    results = await session.exec(statement)
                    items = results.all()
                    
                    # Serialize
                    data = [item.model_dump(mode='json') for item in items]
                    zipf.writestr(f"{table_name}.json", json.dumps(data, indent=2))

            # 2. Add Recordings
            if recordings_dir.exists():
                for root, dirs, files in os.walk(recordings_dir):
                    for file in files:
                        file_path = os.path.join(root, file)
                        arcname = os.path.join("recordings", os.path.relpath(file_path, recordings_dir))
                        zipf.write(file_path, arcname)

            # 3. Add Config
            if config_path.exists():
                zipf.write(config_path, "config.json")

        return temp_zip.name

    @staticmethod
    async def restore_backup(zip_path: str, clear_existing: bool = False):
        path_manager = PathManager()
        recordings_dir = path_manager.recordings_directory
        config_path = path_manager.config_path
        
        # ID Mapping for additive restore
        # Map: table_name -> { old_id: new_id }
        id_map: Dict[str, Dict[int, int]] = {name: {} for name, _ in MODELS}

        with zipfile.ZipFile(zip_path, 'r') as zipf:
            # 1. Clear Existing Data if requested
            if clear_existing:
                # Clear DB
                async with Session(engine) as session:
                    # Delete in reverse order
                    for table_name, model_cls in reversed(MODELS):
                        await session.exec(delete(model_cls))
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
            async with Session(engine) as session:
                for table_name, model_cls in MODELS:
                    if f"{table_name}.json" not in zipf.namelist():
                        continue
                    
                    data = json.loads(zipf.read(f"{table_name}.json"))
                    
                    for item_data in data:
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
