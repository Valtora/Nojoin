import zipfile
import json
import os
import shutil
import tempfile
import subprocess
import logging
from datetime import datetime, timezone
from typing import List, Dict, Any, Type, Tuple
from sqlmodel import select, SQLModel, delete, Session
from sqlalchemy import text
from backend.core.db import async_session_maker, sync_engine
from backend.core.encryption import decrypt_secret, encrypt_secret
from backend.models.calendar import CalendarProvider, CalendarProviderConfig, CalendarConnection, CalendarSource, CalendarEvent
from backend.models.user import User
from backend.models.speaker import GlobalSpeaker, RecordingSpeaker
from backend.models.people_tag import PeopleTag, PeopleTagLink
from backend.models.recording import Recording
from backend.models.tag import Tag, RecordingTag
from backend.models.transcript import Transcript
from backend.models.chat import ChatMessage
from backend.models.task import UserTask
from backend.utils.path_manager import PathManager
from backend.utils.audio import ensure_ffmpeg_in_path
from backend.utils.version import get_installed_version

logger = logging.getLogger(__name__)

MICROSOFT_COMMON_TENANT = "common"
CALENDAR_PROVIDER_ENV_KEYS: Dict[str, Dict[str, str | None]] = {
    CalendarProvider.GOOGLE.value: {
        "client_id": "GOOGLE_OAUTH_CLIENT_ID",
        "client_secret": "GOOGLE_OAUTH_CLIENT_SECRET",
        "tenant_id": None,
    },
    CalendarProvider.MICROSOFT.value: {
        "client_id": "MICROSOFT_OAUTH_CLIENT_ID",
        "client_secret": "MICROSOFT_OAUTH_CLIENT_SECRET",
        "tenant_id": "MICROSOFT_OAUTH_TENANT_ID",
    },
}

# Order matters for restoration
MODELS: List[Tuple[str, Type[SQLModel]]] = [
    ("users", User),
    ("calendar_provider_configs", CalendarProviderConfig),
    ("user_tasks", UserTask),
    ("p_tags", PeopleTag),
    ("global_speakers", GlobalSpeaker),
    ("people_tag_links", PeopleTagLink),
    ("tags", Tag),
    ("recordings", Recording),
    ("calendar_connections", CalendarConnection),
    ("calendar_sources", CalendarSource),
    ("calendar_events", CalendarEvent),
    ("recording_speakers", RecordingSpeaker),
    ("recording_tags", RecordingTag),
    ("transcripts", Transcript),
    ("chat_messages", ChatMessage)
]

class BackupManager:
    # Job tracking: job_id -> {status: str, progress: str, error: str, result: Dict}
    restore_jobs: Dict[str, Dict[str, Any]] = {}

    @staticmethod
    def _get_app_version() -> str:
        return get_installed_version()

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
    def _restore_redacted_sensitive_data(value: Any) -> Any:
        """
        Converts redacted placeholders back to null-like values on restore.
        """
        if isinstance(value, dict):
            return {
                key: BackupManager._restore_redacted_sensitive_data(item)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [BackupManager._restore_redacted_sensitive_data(item) for item in value]
        if value == "REDACTED":
            return None
        return value

    @staticmethod
    def _adapt_record(model_cls: Type[SQLModel], data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Adapts a record dictionary to match the current model schema.
        Removes fields that no longer exist in the model.
        """
        # Gets current field names.
        if hasattr(model_cls, "model_fields"):
            current_fields = model_cls.model_fields.keys()
        else:
            current_fields = model_cls.__fields__.keys()
            
        # Filters data to only include fields that exist in the current model.
        return {k: v for k, v in data.items() if k in current_fields}

    @staticmethod
    def _serialise_backup_table_rows(
        table_name: str,
        items: List[SQLModel],
    ) -> List[Dict[str, Any]]:
        if table_name == "calendar_provider_configs":
            data = BackupManager._serialise_calendar_provider_configs(items)
        elif table_name == "calendar_connections":
            data = BackupManager._serialise_calendar_connections(items)
        else:
            data = [item.model_dump(mode="json") for item in items]

        if table_name == "recordings":
            for item in data:
                if "audio_path" in item and item["audio_path"]:
                    item["audio_path"] = BackupManager._build_backup_recording_audio_path(
                        item["audio_path"]
                    )
                    item["file_size_bytes"] = None

                # Proxy files are not backed up directly; they are regenerated after restore.
                item["proxy_path"] = None

        if table_name == "users":
            for item in data:
                if "settings" in item and item["settings"]:
                    item["settings"] = BackupManager._redact_sensitive_data(item["settings"])

        return data

    @staticmethod
    def _enqueue_proxy_generation(recording_id: int) -> None:
        from backend.worker.tasks import generate_proxy_task

        generate_proxy_task.delay(recording_id)

    @staticmethod
    def _serialise_calendar_provider_configs(
        rows: List[CalendarProviderConfig],
    ) -> List[Dict[str, Any]]:
        serialised: List[Dict[str, Any]] = []
        rows_by_provider = {row.provider: row for row in rows}
        handled_providers: set[str] = set()

        for provider, env_keys in CALENDAR_PROVIDER_ENV_KEYS.items():
            row = rows_by_provider.get(provider)
            env_client_id = os.getenv(env_keys["client_id"] or "") if env_keys["client_id"] else None
            env_client_secret = os.getenv(env_keys["client_secret"] or "") if env_keys["client_secret"] else None
            env_tenant_id = os.getenv(env_keys["tenant_id"] or "") if env_keys["tenant_id"] else None

            if row is None:
                has_env_config = bool(
                    env_client_id
                    or env_client_secret
                    or (provider == CalendarProvider.MICROSOFT.value and env_tenant_id)
                )
                if not has_env_config:
                    continue

                serialised.append(
                    {
                        "provider": provider,
                        "client_id": env_client_id,
                        "client_secret": env_client_secret,
                        "tenant_id": env_tenant_id or (MICROSOFT_COMMON_TENANT if provider == CalendarProvider.MICROSOFT.value else None),
                        "enabled": True,
                    }
                )
                handled_providers.add(provider)
                continue

            row_data = row.model_dump(mode="json")
            decrypted_secret = decrypt_secret(row.client_secret_encrypted)

            if row.enabled is False:
                row_data["client_id"] = row.client_id
                row_data["tenant_id"] = row.tenant_id or (MICROSOFT_COMMON_TENANT if provider == CalendarProvider.MICROSOFT.value else None)
                row_data["client_secret"] = decrypted_secret
            else:
                row_data["client_id"] = row.client_id or env_client_id
                row_data["tenant_id"] = row.tenant_id or env_tenant_id or (MICROSOFT_COMMON_TENANT if provider == CalendarProvider.MICROSOFT.value else None)
                row_data["client_secret"] = decrypted_secret or env_client_secret

            row_data.pop("client_secret_encrypted", None)
            serialised.append(row_data)
            handled_providers.add(provider)

        for row in rows:
            if row.provider in handled_providers:
                continue
            row_data = row.model_dump(mode="json")
            row_data["client_secret"] = decrypt_secret(row.client_secret_encrypted)
            row_data.pop("client_secret_encrypted", None)
            serialised.append(row_data)

        return serialised

    @staticmethod
    def _serialise_calendar_connections(
        rows: List[CalendarConnection],
    ) -> List[Dict[str, Any]]:
        serialised: List[Dict[str, Any]] = []
        for row in rows:
            row_data = row.model_dump(mode="json")
            row_data["access_token"] = decrypt_secret(row.access_token_encrypted)
            row_data["refresh_token"] = decrypt_secret(row.refresh_token_encrypted)
            row_data.pop("access_token_encrypted", None)
            row_data.pop("refresh_token_encrypted", None)
            serialised.append(row_data)
        return serialised

    @staticmethod
    def _prepare_calendar_provider_config_for_restore(
        data: Dict[str, Any],
    ) -> Dict[str, Any]:
        restored = data.copy()
        client_secret = restored.pop("client_secret", None)
        if "client_secret_encrypted" not in restored or client_secret is not None:
            stripped_secret = client_secret.strip() if isinstance(client_secret, str) else client_secret
            restored["client_secret_encrypted"] = encrypt_secret(stripped_secret) if stripped_secret else None

        if restored.get("provider") == CalendarProvider.GOOGLE.value:
            restored["tenant_id"] = None
        elif restored.get("provider") == CalendarProvider.MICROSOFT.value and not restored.get("tenant_id"):
            restored["tenant_id"] = MICROSOFT_COMMON_TENANT

        return restored

    @staticmethod
    def _prepare_calendar_connection_for_restore(
        data: Dict[str, Any],
    ) -> Dict[str, Any]:
        restored = data.copy()
        access_token = restored.pop("access_token", None)
        refresh_token = restored.pop("refresh_token", None)

        if "access_token_encrypted" not in restored or access_token is not None:
            stripped_access = access_token.strip() if isinstance(access_token, str) else access_token
            restored["access_token_encrypted"] = encrypt_secret(stripped_access) if stripped_access else None

        if "refresh_token_encrypted" not in restored or refresh_token is not None:
            stripped_refresh = refresh_token.strip() if isinstance(refresh_token, str) else refresh_token
            restored["refresh_token_encrypted"] = encrypt_secret(stripped_refresh) if stripped_refresh else None

        return restored

    @staticmethod
    def _get_recording_subpath(audio_path: str | None) -> str | None:
        if not audio_path:
            return None

        normalized = str(audio_path).strip().replace("\\", "/")
        parts = [part for part in normalized.split("/") if part and part != "."]
        if not parts:
            return None

        for index in range(len(parts) - 1, -1, -1):
            if parts[index].lower() == "recordings":
                tail = parts[index + 1 :]
                if tail:
                    return "/".join(tail)
                break

        return parts[-1]

    @staticmethod
    def _get_recording_identity(audio_path: str | None) -> str | None:
        subpath = BackupManager._get_recording_subpath(audio_path)
        if not subpath:
            return None

        stem, _ = os.path.splitext(subpath)
        return os.path.normcase(stem)

    @staticmethod
    def _normalise_meeting_uid(meeting_uid: Any) -> str | None:
        if meeting_uid is None:
            return None

        normalized = str(meeting_uid).strip().lower()
        return normalized or None

    @staticmethod
    def _get_recording_match_key(
        audio_path: str | None,
        meeting_uid: Any = None,
    ) -> str | None:
        normalized_uid = BackupManager._normalise_meeting_uid(meeting_uid)
        if normalized_uid:
            return f"meeting_uid:{normalized_uid}"

        legacy_identity = BackupManager._get_recording_identity(audio_path)
        if legacy_identity:
            return f"audio_path:{legacy_identity}"

        return None

    @staticmethod
    def _build_backup_recording_audio_path(audio_path: str | None) -> str | None:
        subpath = BackupManager._get_recording_subpath(audio_path)
        if not subpath:
            return None

        stem, _ = os.path.splitext(subpath)
        return os.path.join("recordings", stem + ".opus")

    @staticmethod
    def _build_runtime_recording_audio_path(
        audio_path: str | None,
        recordings_dir: str | os.PathLike[str],
    ) -> str | None:
        subpath = BackupManager._get_recording_subpath(audio_path)
        if not subpath:
            return None

        stem, _ = os.path.splitext(subpath)
        target_abs = os.path.abspath(os.path.join(os.fspath(recordings_dir), stem + ".opus"))
        cwd = os.path.abspath(os.getcwd())

        try:
            if os.path.commonpath([cwd, target_abs]) == cwd:
                return os.path.relpath(target_abs, cwd)
        except ValueError:
            pass

        return target_abs

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
    def _create_backup_sync(
        recordings_dir: PathManager, 
        config_path: PathManager, 
        db_dump: Dict[str, str],
        include_audio: bool
    ) -> str:
        """
        Synchronous method to handle heavy file compression and zipping.
        Runs in a thread to prevent blocking the main event loop.
        """
        path_manager = PathManager()
        
        # Creates a temporary file for the zip explicitly in /tmp (mounted as volume).
        temp_zip = tempfile.NamedTemporaryFile(delete=False, suffix=".zip", dir="/tmp")
        temp_zip.close()

        try:
            with zipfile.ZipFile(temp_zip.name, 'w', zipfile.ZIP_DEFLATED) as zipf:
                # 1. Write DB Dump
                for filename, content in db_dump.items():
                    zipf.writestr(filename, content)

                # 2. Add Recordings (Smart Deduplication & Audio Toggle)
                if include_audio and recordings_dir.exists():
                    # Strategies:
                    # 1. Prefer .opus (already compressed)
                    # 2. Fallback to .wav/.mp3 etc (requires compression)
                    
                    file_map: Dict[str, str] = {}
                    
                    for root, dirs, files in os.walk(recordings_dir):
                        for file in files:
                            file_path = os.path.join(root, file)
                            ext = os.path.splitext(file)[1].lower()
                            base_name = os.path.splitext(file)[0] # Usually the UUID
                            
                            # Include only specific audio formats.
                            if ext not in ['.wav', '.mp3', '.m4a', '.ogg', '.flac', '.opus']:
                                pass
                            
                            if ext == '.opus':
                                file_map[base_name] = file_path # Overrides others
                            elif base_name not in file_map:
                                file_map[base_name] = file_path # Candidate
                            
                    # Now process the map
                    added_paths = set()
                    
                    for base_name, file_path in file_map.items():
                        ext = os.path.splitext(file_path)[1].lower()
                        
                        # Calculate arcname (always .opus for audio)
                        rel_path_base = os.path.relpath(os.path.dirname(file_path), recordings_dir)
                        if rel_path_base == ".":
                            arcname = os.path.join("recordings", base_name + ".opus")
                        else:
                            arcname = os.path.join("recordings", rel_path_base, base_name + ".opus")
                            
                        if arcname in added_paths:
                            continue # Skip duplicate
                            
                        try:
                            if ext == '.opus':
                                zipf.write(file_path, arcname)
                            elif ext in ['.wav', '.mp3', '.m4a', '.ogg', '.flac']:
                                # Compress
                                opus_path = BackupManager._compress_to_opus(file_path)
                                zipf.write(opus_path, arcname)
                                os.remove(opus_path)
                            
                            added_paths.add(arcname)
                        except Exception as e:
                            logger.error(f"Failed to process audio {file_path}: {e}")
                            # Continue with other files

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
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "include_audio": include_audio,
                    "contains_restorable_calendar_credentials": True,
                }
                zipf.writestr("backup_info.json", json.dumps(backup_info, indent=2))

            return temp_zip.name
            
        except Exception as e:
            # Cleanup temp zip if failed
            if os.path.exists(temp_zip.name):
                os.remove(temp_zip.name)
            raise e

    @staticmethod
    def _topological_sort(data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Sorts tags so that parents appear before children.
        """
        # 1. Build index and adjacency
        by_id = {item["id"]: item for item in data if "id" in item}
        children_map: Dict[Any, List[Dict[str, Any]]] = {} # parent_id -> list of children
        roots = []

        for item in data:
            parent_id = item.get("parent_id")
            if parent_id and parent_id in by_id:
                if parent_id not in children_map:
                    children_map[parent_id] = []
                children_map[parent_id].append(item)
            else:
                roots.append(item)

        # 2. Flatten
        sorted_items = []
        queue = list(roots) 
        
        # Sort roots by ID to be deterministic
        queue.sort(key=lambda x: x.get("id", 0))

        while queue:
            node = queue.pop(0)
            sorted_items.append(node)
            
            node_id = node.get("id")
            if node_id in children_map:
                children = children_map[node_id]
                # Sort children by ID
                children.sort(key=lambda x: x.get("id", 0))
                # Append children to the end of the queue (BFS traversal).
                queue.extend(children)
        
        # checks for cycles or disconnected items (shouldn't happen in valid trees)
        # If we missed any, just append them at the end (fallback)
        if len(sorted_items) < len(data):
            processed_ids = {x.get("id") for x in sorted_items}
            for item in data:
                if item.get("id") not in processed_ids:
                    sorted_items.append(item)
                    
        return sorted_items

    @staticmethod
    async def create_backup(include_audio: bool = True) -> str:
        path_manager = PathManager()
        recordings_dir = path_manager.recordings_directory
        config_path = path_manager.config_path
        
        import asyncio

        # 1. Dump Database (Async)
        db_dump = {}
        async with async_session_maker() as session:
            for table_name, model_cls in MODELS:
                statement = select(model_cls)
                
                # Safe Order for Tags (Parents First)
                if table_name in ["tags", "p_tags"]:
                    # Order by parent_id NULLS FIRST, then ID
                    # This isn't strictly perfect for deep nesting without recursive CTE,
                    # but it helps simple 1-level nesting which is common.
                    statement = statement.order_by(model_cls.parent_id.nullsfirst(), model_cls.id)

                results = await session.execute(statement)
                items = results.scalars().all()

                data = BackupManager._serialise_backup_table_rows(table_name, items)

                db_dump[f"{table_name}.json"] = json.dumps(data, indent=2)

        # 2. Heavy Lifting in Thread
        return await asyncio.to_thread(
            BackupManager._create_backup_sync,
            recordings_dir,
            config_path,
            db_dump,
            include_audio
        )

    @staticmethod
    def create_backup_blocking(include_audio: bool = True) -> str:
        path_manager = PathManager()
        recordings_dir = path_manager.recordings_directory
        config_path = path_manager.config_path

        db_dump: Dict[str, str] = {}
        with Session(sync_engine) as session:
            for table_name, model_cls in MODELS:
                statement = select(model_cls)

                if table_name in ["tags", "p_tags"]:
                    statement = statement.order_by(model_cls.parent_id.nullsfirst(), model_cls.id)

                items = session.exec(statement).all()
                data = BackupManager._serialise_backup_table_rows(table_name, items)
                db_dump[f"{table_name}.json"] = json.dumps(data, indent=2)

        return BackupManager._create_backup_sync(
            recordings_dir,
            config_path,
            db_dump,
            include_audio,
        )

    @staticmethod
    def _restore_backup_sync(
        job_id: str,
        zip_path: str,
        clear_existing: bool,
        overwrite_existing: bool,
        recordings_dir: PathManager,
        config_path: PathManager,
        user_data_dir: PathManager
    ):
        """
        Synchronous implementation of backup restoration to be run in a separate thread.
        """
        BackupManager.restore_jobs[job_id]["status"] = "processing"
        BackupManager.restore_jobs[job_id]["progress"] = "Starting..."
        logger.info(f"Starting synchronous restore process for {zip_path}")

        
        # ID Mapping for additive restore
        # Map: table_name -> { old_id: new_id }
        id_map: Dict[str, Dict[int, int]] = {name: {} for name, _ in MODELS}
        
        # Map: preferred meeting identity key -> new_id.
        restored_recording_keys: Dict[str, int] = {}

        # Map: preferred meeting identity key -> existing recording.
        existing_recordings_by_identity: Dict[str, Recording] = {}

        # Set of skipped recording IDs (old_id) - used to skip children
        skipped_recording_ids = set()

        # Deferred self-referential remaps for recording speakers.
        pending_recording_speaker_merges: List[Tuple[int, int]] = []

        # Recordings restored with audio files need a fresh playback proxy.
        recordings_requiring_proxy: set[int] = set()

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
                logger.info("Clearing existing data...")
                # Clear DB, BUT SKIP USERS to prevent lockout
                # NOTE: We need a new session here since we are in a thread
                from backend.core.db import sync_engine
                from sqlmodel import Session
                
                with Session(sync_engine) as session:
                    # Delete in reverse order
                    for table_name, model_cls in reversed(MODELS):
                        if table_name == "users":
                            continue
                        session.exec(delete(model_cls))
                    session.commit()
                
                # Clear Recordings
                if recordings_dir.exists():
                    shutil.rmtree(recordings_dir)
                recordings_dir.mkdir(parents=True, exist_ok=True)
                logger.info("Existing data cleared.")
                BackupManager.restore_jobs[job_id]["progress"] = "Old data cleared"

            # 2. Restore Files
            extracted_files = set()
            
            # Extract recordings
            logger.info("Extracting files...")
            BackupManager.restore_jobs[job_id]["progress"] = "Extracting files..."
            for file in zipf.namelist():
                if file.startswith("recordings/"):
                    # Zip Slip Mitigation
                    target_path = os.path.abspath(os.path.join(user_data_dir, file))
                    if not target_path.startswith(os.path.abspath(user_data_dir) + os.sep):
                        error_msg = f"Zip Slip detected: Skipping malicious file path {file}"
                        logger.error(error_msg)
                        raise ValueError(error_msg)
                        
                    # Optimization: Extract all files. If the DB record is skipped,
                    # the file will be orphaned but cleaned up later.
                    zipf.extract(file, user_data_dir)
                    extracted_files.add(file)
                elif file == "config.json":
                    if clear_existing:
                        # Only restore config if we are clearing data, otherwise keep current system config
                        try:
                            # Zip Slip Mitigation for config
                            target_path = os.path.abspath(os.path.join(user_data_dir, file))
                            if not target_path.startswith(os.path.abspath(user_data_dir) + os.sep):
                                error_msg = f"Zip Slip detected: Skipping malicious file path {file}"
                                logger.error(error_msg)
                                raise ValueError(error_msg)

                            new_config = json.loads(zipf.read("config.json"))
                            if config_path.exists():
                                current_config = json.loads(config_path.read_text())
                                # Merge: Update current with new, but keep current if new is REDACTED
                                for k, v in new_config.items():
                                    if v != "REDACTED":
                                        current_config[k] = v
                                        
                                # Write to string first then file
                                config_path_obj = PathManager().config_path
                                config_path_obj.write_text(json.dumps(current_config, indent=2))
                            else:
                                zipf.extract(file, user_data_dir)
                        except Exception as e:
                            if isinstance(e, ValueError) and "Zip Slip detected" in str(e):
                                raise
                            logger.error(f"Failed to restore config: {e}")
            logger.info("File extraction complete.")

            # 2a. Pre-flight Cleanup (Overwrite Strategy)
            if overwrite_existing and not clear_existing:
                if "recordings.json" in zipf.namelist():
                    try:
                        rec_data = json.loads(zipf.read("recordings.json"))
                        recording_keys = {
                            BackupManager._get_recording_match_key(
                                item.get("audio_path"),
                                item.get("meeting_uid"),
                            )
                            for item in rec_data
                            if item.get("audio_path")
                        }
                        recording_keys.discard(None)
                        
                        if recording_keys:
                            from backend.core.db import sync_engine
                            from sqlmodel import Session
                            with Session(sync_engine) as session:
                                existing_rows = session.exec(select(Recording)).all()
                                existing_ids = [
                                    row.id
                                    for row in existing_rows
                                    if BackupManager._get_recording_match_key(
                                        row.audio_path,
                                        getattr(row, "meeting_uid", None),
                                    )
                                    in recording_keys
                                ]
                                
                                if existing_ids:
                                    logger.info(f"Overwrite: Pre-deleting {len(existing_ids)} conflicting recordings.")
                                    # Specific delete to trigger cascades if needed (though delete from recordings usually cascades)
                                    session.exec(delete(Recording).where(Recording.id.in_(existing_ids)))
                                    session.commit()
                    except Exception as e:
                        logger.error(f"Pre-flight cleanup failed: {e}")

            # 3. Restore Database
            logger.info("Restoring database records...")
            BackupManager.restore_jobs[job_id]["progress"] = "Restoring database..."
            from backend.core.db import sync_engine
            from sqlmodel import Session
            
            with Session(sync_engine) as session:
                for table_name, model_cls in MODELS:
                    if f"{table_name}.json" not in zipf.namelist():
                        continue
                    
                    try:
                        data = json.loads(zipf.read(f"{table_name}.json"))
                    except Exception as e:
                        logger.error(f"Failed to read/parse {table_name}.json: {e}")
                        continue
                    
                    # Topological Sort for Tags to ensure Parents are created first
                    if table_name in ["tags", "p_tags"]:
                        data = BackupManager._topological_sort(data)

                    count = 0
                    for item_data in data:
                        if table_name == "calendar_provider_configs":
                            item_data = BackupManager._prepare_calendar_provider_config_for_restore(item_data)
                        elif table_name == "calendar_connections":
                            item_data = BackupManager._prepare_calendar_connection_for_restore(item_data)

                        # Adapt record to current schema (handle removed columns)
                        item_data = BackupManager._adapt_record(model_cls, item_data)

                        old_id = item_data.get("id")
                        
                        # Handle Conflict / Additive Logic
                        
                        # Special handling for Users: Resolve by username
                        if table_name == "users":
                            if item_data.get("settings"):
                                item_data["settings"] = BackupManager._restore_redacted_sensitive_data(
                                    item_data["settings"]
                                )

                            username = item_data.get("username")
                            existing_user = session.exec(select(User).where(User.username == username)).first()
                            
                            if existing_user:
                                # User exists. Map old_id to existing_id.
                                # Do NOT overwrite the user (security risk, plus passwords etc).
                                if old_id is not None:
                                    id_map["users"][old_id] = existing_user.id
                                continue # Skip inserting this user
                            
                        # Special handling for Recordings: Resolve by audio_path
                        elif table_name == "recordings":
                            audio_path = item_data.get("audio_path")
                            meeting_uid = item_data.get("meeting_uid")
                            recording_key = BackupManager._get_recording_match_key(
                                audio_path,
                                meeting_uid,
                            )

                            if not existing_recordings_by_identity:
                                existing_rows = session.exec(select(Recording)).all()
                                existing_recordings_by_identity = {
                                    identity: row
                                    for row in existing_rows
                                    if (
                                        identity := BackupManager._get_recording_match_key(
                                            row.audio_path,
                                            getattr(row, "meeting_uid", None),
                                        )
                                    )
                                }

                            if not recording_key:
                                logger.warning("Skipping recording restore because audio_path is missing or invalid")
                                continue
                            
                            # DUPLICATE IN BACKUP CHECK:
                            # If we already restored this meeting in this session, map to that ID and SKIP.
                            if recording_key in restored_recording_keys:
                                logger.warning(
                                    f"Duplicate recording in backup JSON: {audio_path}. Linking old_id {old_id} to existing new_id {restored_recording_keys[recording_key]}"
                                )
                                if old_id is not None:
                                    id_map["recordings"][old_id] = restored_recording_keys[recording_key]
                                continue # Skip insert

                            existing_rec = existing_recordings_by_identity.get(recording_key)
                            
                            if existing_rec:
                                # Conflict detected. If overwrite_existing is True, this record should have been
                                # removed during pre-flight cleanup, suggesting a potential race condition.
                                
                                if overwrite_existing:
                                    # Fallback: Delete row
                                    # NOTE: Pre-flight should have caught this. If we are here, it's a straggler or race condition.
                                    logger.warning(f"Fallback delete triggered for audio_path={audio_path}. Deleting ID {existing_rec.id}.")
                                    session.delete(existing_rec)
                                    session.flush()
                                else:
                                    # Strategy: SKIP (Safe Merge)
                                    # Uses existing ID map.
                                    if old_id is not None:
                                        id_map["recordings"][old_id] = existing_rec.id
                                        skipped_recording_ids.add(old_id)
                                        # Tracks as restored/processed so subsequent duplicates map to it.
                                        restored_recording_keys[recording_key] = existing_rec.id
                                    continue

                            normalized_meeting_uid = BackupManager._normalise_meeting_uid(meeting_uid)
                            if normalized_meeting_uid:
                                item_data["meeting_uid"] = normalized_meeting_uid
                            else:
                                item_data.pop("meeting_uid", None)

                            # Backups do not preserve proxy files; regenerate them after restore.
                            item_data["proxy_path"] = None

                            item_data["audio_path"] = BackupManager._build_runtime_recording_audio_path(
                                audio_path,
                                recordings_dir,
                            ) or audio_path
                        
                        else:
                            # Child Records: Skip if parent recording was skipped
                            if "recording_id" in item_data:
                                old_rec_id = item_data["recording_id"]
                                if old_rec_id in skipped_recording_ids:
                                    # Parent was skipped, so the child is skipped to avoid duplication/errors.
                                    continue

                            # Tags: name + user_id is unique?
                            if table_name == "tags":
                                tag_name = item_data.get("name")
                                # Fix possible FK resolution before check
                                user_id = item_data.get("user_id")
                                if user_id in id_map["users"]:
                                    user_id = id_map["users"][user_id]
                                
                                
                                # Check existence
                                existing_tag = session.exec(
                                    select(Tag)
                                    .where(Tag.name == tag_name)
                                    .where(Tag.user_id == user_id)
                                ).first()

                                if existing_tag:
                                    if old_id is not None:
                                        id_map["tags"][old_id] = existing_tag.id
                                    continue

                            elif table_name == "p_tags":
                                tag_name = item_data.get("name")
                                user_id_val = item_data.get("user_id")
                                
                                # Remap user_id before checking existence
                                if user_id_val in id_map["users"]:
                                    user_id_val = id_map["users"][user_id_val]
                                
                                # Check existence
                                existing_p_tag = session.exec(
                                    select(PeopleTag)
                                    .where(PeopleTag.name == tag_name)
                                    .where(PeopleTag.user_id == user_id_val)
                                ).first()
                                
                                if existing_p_tag:
                                    if old_id is not None:
                                        id_map["p_tags"][old_id] = existing_p_tag.id
                                    continue

                        # Remap Foreign Keys for insertion
                        
                        # Removes ID to allow the database to generate a new one.
                        if "id" in item_data:
                            del item_data["id"]

                        old_recording_speaker_merge_id = None

                        # Remaps Foreign Keys.
                        if table_name == "calendar_provider_configs":
                            candidate = model_cls.model_validate(item_data)
                            existing_config = session.exec(
                                select(CalendarProviderConfig).where(
                                    CalendarProviderConfig.provider == candidate.provider
                                )
                            ).first()

                            if existing_config:
                                if overwrite_existing:
                                    existing_config.client_id = candidate.client_id
                                    existing_config.client_secret_encrypted = candidate.client_secret_encrypted
                                    existing_config.tenant_id = candidate.tenant_id
                                    existing_config.enabled = candidate.enabled
                                    session.add(existing_config)
                                else:
                                    updated = False
                                    if not existing_config.client_id and candidate.client_id:
                                        existing_config.client_id = candidate.client_id
                                        updated = True
                                    if (
                                        not existing_config.client_secret_encrypted
                                        and candidate.client_secret_encrypted
                                    ):
                                        existing_config.client_secret_encrypted = candidate.client_secret_encrypted
                                        updated = True
                                    if (
                                        candidate.provider == CalendarProvider.MICROSOFT.value
                                        and not existing_config.tenant_id
                                        and candidate.tenant_id
                                    ):
                                        existing_config.tenant_id = candidate.tenant_id
                                        updated = True
                                    if updated:
                                        session.add(existing_config)

                                if old_id is not None:
                                    id_map["calendar_provider_configs"][old_id] = existing_config.id
                                continue

                        elif table_name == "calendar_connections":
                            if item_data.get("user_id") in id_map["users"]:
                                item_data["user_id"] = id_map["users"][item_data["user_id"]]
                            else:
                                continue

                            candidate = model_cls.model_validate(item_data)
                            existing_connection = session.exec(
                                select(CalendarConnection)
                                .where(CalendarConnection.user_id == candidate.user_id)
                                .where(CalendarConnection.provider == candidate.provider)
                                .where(
                                    CalendarConnection.provider_account_id
                                    == candidate.provider_account_id
                                )
                            ).first()

                            if existing_connection:
                                if overwrite_existing:
                                    existing_connection.email = candidate.email
                                    existing_connection.display_name = candidate.display_name
                                    existing_connection.access_token_encrypted = candidate.access_token_encrypted
                                    existing_connection.refresh_token_encrypted = candidate.refresh_token_encrypted
                                    existing_connection.granted_scopes = candidate.granted_scopes
                                    existing_connection.token_expires_at = candidate.token_expires_at
                                    existing_connection.sync_status = candidate.sync_status
                                    existing_connection.sync_error = candidate.sync_error
                                    existing_connection.last_sync_started_at = candidate.last_sync_started_at
                                    existing_connection.last_sync_completed_at = candidate.last_sync_completed_at
                                    existing_connection.last_synced_at = candidate.last_synced_at
                                    session.add(existing_connection)
                                else:
                                    updated = False
                                    for field in (
                                        "email",
                                        "display_name",
                                        "access_token_encrypted",
                                        "refresh_token_encrypted",
                                        "token_expires_at",
                                    ):
                                        if (
                                            not getattr(existing_connection, field)
                                            and getattr(candidate, field)
                                        ):
                                            setattr(existing_connection, field, getattr(candidate, field))
                                            updated = True

                                    if (
                                        not existing_connection.granted_scopes
                                        and candidate.granted_scopes
                                    ):
                                        existing_connection.granted_scopes = candidate.granted_scopes
                                        updated = True

                                    existing_sync_marker = (
                                        existing_connection.last_synced_at
                                        or existing_connection.last_sync_completed_at
                                    )
                                    candidate_sync_marker = (
                                        candidate.last_synced_at
                                        or candidate.last_sync_completed_at
                                    )
                                    if candidate_sync_marker and (
                                        existing_sync_marker is None
                                        or candidate_sync_marker >= existing_sync_marker
                                    ):
                                        existing_connection.sync_status = candidate.sync_status
                                        existing_connection.sync_error = candidate.sync_error
                                        existing_connection.last_sync_started_at = candidate.last_sync_started_at
                                        existing_connection.last_sync_completed_at = candidate.last_sync_completed_at
                                        existing_connection.last_synced_at = candidate.last_synced_at
                                        updated = True

                                    if updated:
                                        session.add(existing_connection)

                                if old_id is not None:
                                    id_map["calendar_connections"][old_id] = existing_connection.id
                                continue

                        elif table_name == "calendar_sources":
                            if item_data.get("connection_id") in id_map["calendar_connections"]:
                                item_data["connection_id"] = id_map["calendar_connections"][
                                    item_data["connection_id"]
                                ]
                            else:
                                continue

                            candidate = model_cls.model_validate(item_data)
                            existing_source = session.exec(
                                select(CalendarSource)
                                .where(CalendarSource.connection_id == candidate.connection_id)
                                .where(
                                    CalendarSource.provider_calendar_id
                                    == candidate.provider_calendar_id
                                )
                            ).first()

                            if existing_source:
                                if overwrite_existing:
                                    existing_source.name = candidate.name
                                    existing_source.description = candidate.description
                                    existing_source.time_zone = candidate.time_zone
                                    existing_source.colour = candidate.colour
                                    existing_source.user_colour = candidate.user_colour
                                    existing_source.is_primary = candidate.is_primary
                                    existing_source.is_read_only = candidate.is_read_only
                                    existing_source.is_selected = candidate.is_selected
                                    existing_source.sync_cursor = candidate.sync_cursor
                                    existing_source.last_synced_at = candidate.last_synced_at
                                    existing_source.sync_window_start = candidate.sync_window_start
                                    existing_source.sync_window_end = candidate.sync_window_end
                                    session.add(existing_source)
                                else:
                                    updated = False
                                    for field in ("name", "description", "time_zone"):
                                        candidate_value = getattr(candidate, field)
                                        if candidate_value and getattr(existing_source, field) != candidate_value:
                                            setattr(existing_source, field, candidate_value)
                                            updated = True

                                    if candidate.colour and existing_source.colour != candidate.colour:
                                        existing_source.colour = candidate.colour
                                        updated = True
                                    if not existing_source.user_colour and candidate.user_colour:
                                        existing_source.user_colour = candidate.user_colour
                                        updated = True
                                    if candidate.is_primary and not existing_source.is_primary:
                                        existing_source.is_primary = True
                                        updated = True
                                    if candidate.is_read_only and not existing_source.is_read_only:
                                        existing_source.is_read_only = True
                                        updated = True
                                    if candidate.is_selected and not existing_source.is_selected:
                                        existing_source.is_selected = True
                                        updated = True

                                    if candidate.last_synced_at and (
                                        existing_source.last_synced_at is None
                                        or candidate.last_synced_at >= existing_source.last_synced_at
                                    ):
                                        existing_source.sync_cursor = candidate.sync_cursor
                                        existing_source.last_synced_at = candidate.last_synced_at
                                        existing_source.sync_window_start = candidate.sync_window_start
                                        existing_source.sync_window_end = candidate.sync_window_end
                                        updated = True

                                    if updated:
                                        session.add(existing_source)

                                if old_id is not None:
                                    id_map["calendar_sources"][old_id] = existing_source.id
                                continue

                        elif table_name == "calendar_events":
                            if item_data.get("calendar_id") in id_map["calendar_sources"]:
                                item_data["calendar_id"] = id_map["calendar_sources"][
                                    item_data["calendar_id"]
                                ]
                            else:
                                continue

                            candidate = model_cls.model_validate(item_data)
                            existing_event = session.exec(
                                select(CalendarEvent)
                                .where(CalendarEvent.calendar_id == candidate.calendar_id)
                                .where(CalendarEvent.provider_event_id == candidate.provider_event_id)
                            ).first()

                            if existing_event:
                                should_replace_event = overwrite_existing or (
                                    candidate.external_updated_at is not None
                                    and (
                                        existing_event.external_updated_at is None
                                        or candidate.external_updated_at
                                        >= existing_event.external_updated_at
                                    )
                                )

                                if should_replace_event:
                                    existing_event.title = candidate.title
                                    existing_event.status = candidate.status
                                    existing_event.is_all_day = candidate.is_all_day
                                    existing_event.starts_at = candidate.starts_at
                                    existing_event.ends_at = candidate.ends_at
                                    existing_event.start_date = candidate.start_date
                                    existing_event.end_date = candidate.end_date
                                    existing_event.location_text = candidate.location_text
                                    existing_event.meeting_url = candidate.meeting_url
                                    existing_event.source_url = candidate.source_url
                                    existing_event.external_updated_at = candidate.external_updated_at
                                    session.add(existing_event)
                                else:
                                    updated = False
                                    for field in ("location_text", "meeting_url", "source_url"):
                                        if not getattr(existing_event, field) and getattr(candidate, field):
                                            setattr(existing_event, field, getattr(candidate, field))
                                            updated = True
                                    if updated:
                                        session.add(existing_event)

                                if old_id is not None:
                                    id_map["calendar_events"][old_id] = existing_event.id
                                continue

                        elif table_name == "global_speakers":
                            if item_data.get("user_id") in id_map["users"]:
                                item_data["user_id"] = id_map["users"][item_data["user_id"]]
                            else:
                                continue

                            # Checks for existing duplicates to prevent redundant entries.
                            speaker_name = item_data.get("name")
                            user_id = item_data.get("user_id")
                            
                            existing_speaker = session.exec(
                                select(GlobalSpeaker)
                                .where(GlobalSpeaker.name == speaker_name)
                                .where(GlobalSpeaker.user_id == user_id)
                            ).first()

                            if existing_speaker:
                                if overwrite_existing:
                                    # Updates existing speaker details from backup.
                                    existing_speaker.title = item_data.get("title")
                                    existing_speaker.company = item_data.get("company")
                                    existing_speaker.email = item_data.get("email")
                                    existing_speaker.phone_number = item_data.get("phone_number")
                                    existing_speaker.notes = item_data.get("notes")
                                    existing_speaker.color = item_data.get("color")
                                    if item_data.get("embedding"):
                                        existing_speaker.embedding = item_data.get("embedding")
                                    
                                    session.add(existing_speaker)
                                else:
                                    # INTELLIGENT MERGE: Fill in missing fields only
                                    updated = False
                                    
                                    # CRM Fields
                                    for field in ["title", "company", "email", "phone_number", "notes", "color"]:
                                        if not getattr(existing_speaker, field) and item_data.get(field):
                                            setattr(existing_speaker, field, item_data.get(field))
                                            updated = True
                                    
                                    # Voice Embedding: Restore only if missing locally
                                    if (not existing_speaker.embedding or len(existing_speaker.embedding) == 0) and item_data.get("embedding"):
                                        existing_speaker.embedding = item_data.get("embedding")
                                        updated = True
                                        
                                    if updated:
                                        session.add(existing_speaker)

                                if old_id is not None:
                                    id_map["global_speakers"][old_id] = existing_speaker.id
                                continue

                        elif table_name == "tags":
                            if item_data.get("user_id") in id_map["users"]:
                                item_data["user_id"] = id_map["users"][item_data["user_id"]]
                            else:
                                continue

                            # Remap parent_id if it exists
                            if item_data.get("parent_id") in id_map["tags"]:
                                item_data["parent_id"] = id_map["tags"][item_data["parent_id"]]
                            elif item_data.get("parent_id"):
                                # Fallback: if parent not found despite sort, set to None?
                                # Consistent with p_tags logic
                                item_data["parent_id"] = None
                        
                        elif table_name == "p_tags":
                            if item_data.get("user_id") in id_map["users"]:
                                item_data["user_id"] = id_map["users"][item_data["user_id"]]
                            else:
                                # PeopleTags require a user. If user is missing, skip the tag.
                                pass
                                
                            # Remap parent_id if it exists
                            if item_data.get("parent_id") in id_map["p_tags"]:
                                item_data["parent_id"] = id_map["p_tags"][item_data["parent_id"]]
                            elif item_data.get("parent_id"):
                                # Parent not found (maybe skipped or not yet processed?)
                                item_data["parent_id"] = None

                        elif table_name == "people_tag_links":
                            if item_data.get("global_speaker_id") in id_map["global_speakers"]:
                                item_data["global_speaker_id"] = id_map["global_speakers"][item_data["global_speaker_id"]]
                            else:
                                logger.warning(f"Skipping people_tag_link: global_speaker_id {item_data.get('global_speaker_id')} not found in map.")
                                continue
                            
                            if item_data.get("tag_id") in id_map["p_tags"]:
                                item_data["tag_id"] = id_map["p_tags"][item_data["tag_id"]]
                            else:
                                logger.warning(f"Skipping people_tag_link: tag_id {item_data.get('tag_id')} not found in map.")
                                continue
                            
                            # Checks for duplicates
                            existing_link = session.exec(
                                select(PeopleTagLink)
                                .where(PeopleTagLink.global_speaker_id == item_data["global_speaker_id"])
                                .where(PeopleTagLink.tag_id == item_data["tag_id"])
                            ).first()
                            
                            if existing_link:
                                if old_id is not None:
                                    id_map["people_tag_links"][old_id] = existing_link.id
                                continue

                        elif table_name == "recordings":
                            if item_data.get("user_id") in id_map["users"]:
                                item_data["user_id"] = id_map["users"][item_data["user_id"]]
                            else:
                                continue

                        elif table_name == "user_tasks":
                            if item_data.get("user_id") in id_map["users"]:
                                item_data["user_id"] = id_map["users"][item_data["user_id"]]
                            else:
                                continue

                        elif table_name == "recording_speakers":
                            old_rec_id = item_data.get("recording_id")
                            if old_rec_id in id_map["recordings"]:
                                new_rec_id = id_map["recordings"][old_rec_id]
                                
                                # SANITY CHECK: Does this recording exist?
                                # (Since we are in same transaction, we might need flush first or check local session)
                                # But we're just setting IDs, assume consistency if map is valid.
                                item_data["recording_id"] = new_rec_id
                            else:
                                continue
                            
                            # Safely map Global Speaker ID
                            old_gs_id = item_data.get("global_speaker_id")
                            if old_gs_id and old_gs_id in id_map["global_speakers"]:
                                item_data["global_speaker_id"] = id_map["global_speakers"][old_gs_id]
                            elif old_gs_id:
                                item_data["global_speaker_id"] = None

                            old_recording_speaker_merge_id = item_data.get("merged_into_id")
                            if old_recording_speaker_merge_id is not None:
                                item_data["merged_into_id"] = None
                        
                        elif table_name == "recording_tags":
                            old_rec_id = item_data.get("recording_id")
                            if old_rec_id in id_map["recordings"]:
                                new_rec_id = id_map["recordings"][old_rec_id]
                                item_data["recording_id"] = new_rec_id
                            else:
                                continue
                            
                            if item_data.get("tag_id") in id_map["tags"]:
                                item_data["tag_id"] = id_map["tags"][item_data["tag_id"]]
                            else:
                                logger.warning(f"Skipping recording_tag: tag_id {item_data.get('tag_id')} not found in map.")
                                continue
                            
                            # DUPLICATE CHECK
                            existing_link = session.exec(
                                select(RecordingTag)
                                .where(RecordingTag.recording_id == item_data["recording_id"])
                                .where(RecordingTag.tag_id == item_data["tag_id"])
                            ).first()
                            
                            if existing_link:
                                if old_id is not None:
                                    id_map["recording_tags"][old_id] = existing_link.id
                                continue

                        elif table_name == "transcripts":
                            old_rec_id = item_data.get("recording_id")
                            if old_rec_id in id_map["recordings"]:
                                new_rec_id = id_map["recordings"][old_rec_id]
                                item_data["recording_id"] = new_rec_id
                            else:
                                continue
                                
                            # DUPLICATE CHECK
                            existing_transcript = session.exec(
                                select(Transcript).where(Transcript.recording_id == new_rec_id)
                            ).first()
                            
                            if existing_transcript:
                                if old_id is not None:
                                    id_map["transcripts"][old_id] = existing_transcript.id
                                continue

                        elif table_name == "chat_messages":
                            old_rec_id = item_data.get("recording_id")
                            if old_rec_id in id_map["recordings"]:
                                new_rec_id = id_map["recordings"][old_rec_id]
                                item_data["recording_id"] = new_rec_id
                            else:
                                continue
                            if item_data.get("user_id") in id_map["users"]:
                                item_data["user_id"] = id_map["users"][item_data["user_id"]]
                    
                        # Create instance
                        instance = model_cls.model_validate(item_data)
                        session.add(instance)
                        session.flush() # To get the new ID
                        
                        if old_id is not None:
                            id_map[table_name][old_id] = instance.id

                        if (
                            table_name == "recording_speakers"
                            and old_recording_speaker_merge_id is not None
                        ):
                            pending_recording_speaker_merges.append(
                                (instance.id, old_recording_speaker_merge_id)
                            )
                        
                        # Track restored audio paths for duplicate detection
                        if table_name == "recordings" and hasattr(instance, "audio_path") and instance.audio_path:
                            recording_key = BackupManager._get_recording_match_key(
                                instance.audio_path,
                                getattr(instance, "meeting_uid", None),
                            )
                            if recording_key:
                                restored_recording_keys[recording_key] = instance.id
                                existing_recordings_by_identity[recording_key] = instance
                            if os.path.exists(instance.audio_path):
                                recordings_requiring_proxy.add(instance.id)
                        
                        count += 1
                    
                    logger.info(f"Restored {count} records for {table_name}")

                for recording_speaker_id, old_merge_target_id in pending_recording_speaker_merges:
                    new_merge_target_id = id_map["recording_speakers"].get(old_merge_target_id)
                    if new_merge_target_id is None:
                        continue

                    recording_speaker = session.get(RecordingSpeaker, recording_speaker_id)
                    if recording_speaker is None:
                        continue

                    recording_speaker.merged_into_id = new_merge_target_id
                    session.add(recording_speaker)

                session.commit()
                logger.info("Database restore complete.")
                BackupManager.restore_jobs[job_id]["progress"] = "Database restored"

            for recording_id in sorted(recordings_requiring_proxy):
                try:
                    BackupManager._enqueue_proxy_generation(recording_id)
                except Exception as e:
                    logger.error(
                        "Failed to enqueue proxy generation for restored recording %s: %s",
                        recording_id,
                        e,
                    )

            # 4. Cleanup Orphaned Files
            # Identifies files extracted from the backup that are not referenced in the database.
            logger.info("Cleaning up orphaned files...")
            BackupManager.restore_jobs[job_id]["progress"] = "Cleaning up..."
            from backend.core.db import sync_engine
            from sqlmodel import Session
            with Session(sync_engine) as session:
                # Fetches all audio paths currently in the DB to verify against extracted files.
                all_recordings = session.exec(select(Recording.audio_path)).all()
                valid_paths = set()
                for vp in all_recordings:
                    recording_identity = BackupManager._get_recording_identity(vp)
                    if recording_identity:
                         valid_paths.add(recording_identity)
                
                orphans = []
                for file_path in extracted_files:
                    normalized_path = BackupManager._get_recording_identity(file_path)
                    
                    if normalized_path not in valid_paths:
                        orphans.append(file_path)

                if orphans:
                    logger.info(f"Cleaning up {len(orphans)} orphaned files from restore.")
                    for orphan in orphans:
                        full_path = os.path.join(user_data_dir, orphan)
                        if os.path.exists(full_path):
                            try:
                                os.remove(full_path)
                            except OSError:
                                logger.warning(f"Failed to delete orphaned file: {full_path}")
            logger.info("Restore process finished successfully.")
            BackupManager.restore_jobs[job_id]["status"] = "completed"
            BackupManager.restore_jobs[job_id]["progress"] = "Done"

    @staticmethod
    async def restore_backup(job_id: str, zip_path: str, clear_existing: bool = False, overwrite_existing: bool = False):
        """
        Async wrapper for the synchronous restore process.
        """
        path_manager = PathManager()
        import asyncio
        
        # Initialize job status if not exists (though entry point should have set it)
        if job_id not in BackupManager.restore_jobs:
             BackupManager.restore_jobs[job_id] = {
                 "status": "pending",
                 "progress": "Initializing...",
                 "error": None
             }

        try:
            await asyncio.to_thread(
                BackupManager._restore_backup_sync,
                job_id,
                zip_path,
                clear_existing,
                overwrite_existing,
                path_manager.recordings_directory,
                path_manager.config_path,
                path_manager.user_data_directory
            )
        except Exception as e:
            logger.error(f"Restore failed: {e}", exc_info=True)
            BackupManager.restore_jobs[job_id]["status"] = "failed"
            BackupManager.restore_jobs[job_id]["error"] = str(e)
