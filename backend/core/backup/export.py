"""Building a backup archive.

Progress is reported as the archive is built. Compressing a large library to Opus takes
minutes, and without per-file reporting an export is indistinguishable from a hang.
"""

import json
import logging
import os
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Tuple, Type

from sqlmodel import Session, SQLModel, select

from backend.core.backup import runtime
from backend.core.backup.format import (
    ARCHIVE_QUALITIES,
    ARCHIVE_QUALITY_COMPRESSED,
    BACKUP_EXPORT_DIR,
    BACKUP_FORMAT_VERSION,
)
from backend.core.backup.plans import (
    _AudioPlan,
    _build_audio_plan,
    _build_document_plan,
    _DocumentPlan,
)
from backend.core.backup.records import (
    _redact_sensitive_data,
    _serialise_backup_table_rows,
)
from backend.utils.audio import ensure_ffmpeg_in_path

logger = logging.getLogger(__name__)


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
        "-i",
        input_path,
        "-c:a",
        "libopus",
        "-b:a",
        "64k",  # 64k is good for speech
        "-v",
        "error",
        temp_opus.name,
    ]

    try:
        subprocess.run(cmd, check=True, capture_output=True)
        return temp_opus.name
    except subprocess.CalledProcessError as e:
        if os.path.exists(temp_opus.name):
            os.remove(temp_opus.name)
        raise RuntimeError(f"FFmpeg compression failed: {e}")


def _normalise_archive_quality(archive_quality: str | None) -> str:
    quality = (archive_quality or ARCHIVE_QUALITY_COMPRESSED).strip().lower()
    if quality not in ARCHIVE_QUALITIES:
        logger.warning(
            "Unknown archive quality %r; falling back to %s.",
            archive_quality,
            ARCHIVE_QUALITY_COMPRESSED,
        )
        return ARCHIVE_QUALITY_COMPRESSED
    return quality


def _table_dump_statement(table_name: str, model_cls: Type[SQLModel]):
    statement = select(model_cls)
    if table_name in ["tags", "p_tags"]:
        # Order parents before children (parent_id NULLS FIRST, then id) so the dump
        # lists a parent ahead of any child referencing it. Deeper nesting is
        # reordered by the topological sort on restore.
        statement = statement.order_by(model_cls.parent_id.nullsfirst(), model_cls.id)
    return statement


def _write_audio_members(
    zipf: zipfile.ZipFile,
    audio_plan: _AudioPlan,
    archive_quality: str,
    report: Any,
) -> int:
    """Write one archive member per recording. Returns the count that failed.

    A single unreadable file must not cost the operator the whole archive, so failures
    are counted and reported rather than raised.
    """
    stage = (
        "Compressing audio"
        if archive_quality == ARCHIVE_QUALITY_COMPRESSED
        else "Copying audio"
    )
    failed = 0
    total = len(audio_plan.entries)

    for index, entry in enumerate(audio_plan.entries, start=1):
        report(stage, index, total)
        opus_path: str | None = None
        try:
            if entry.compress:
                opus_path = _compress_to_opus(entry.source_path)
                zipf.write(opus_path, entry.arcname)
            else:
                zipf.write(entry.source_path, entry.arcname)
        except Exception as e:  # noqa: BLE001
            logger.error(f"Failed to process audio {entry.source_path}: {e}")
            failed += 1
        finally:
            # Removed in a finally block so a failed zipf.write does not strand the
            # intermediate file in the shared /tmp volume.
            if opus_path and os.path.exists(opus_path):
                try:
                    os.remove(opus_path)
                except OSError:
                    logger.warning("Failed to remove temporary Opus file %s", opus_path)

    return failed


def _write_document_members(
    zipf: zipfile.ZipFile, document_plan: _DocumentPlan, report: Any
) -> int:
    """Write every attached document. Returns the count that failed."""
    failed = 0
    total = len(document_plan.entries)

    for index, (source_path, arcname) in enumerate(document_plan.entries, start=1):
        report("Adding documents", index, total)
        try:
            zipf.write(source_path, arcname)
        except Exception as e:  # noqa: BLE001
            logger.error(f"Failed to archive document {source_path}: {e}")
            failed += 1

    return failed


def _write_config_member(zipf: zipfile.ZipFile, config_path: Path) -> None:
    """Write the redacted install configuration, if there is one."""
    if not config_path.exists():
        return

    try:
        config_data = _redact_sensitive_data(json.loads(config_path.read_text()))
        zipf.writestr("config.json", json.dumps(config_data, indent=2))
    except Exception as e:  # noqa: BLE001
        logger.error(f"Failed to back up config: {e}")


@dataclass
class _ExportRequest:
    """Everything one export needs, resolved before any file is touched."""

    recordings_dir: Path
    config_path: Path
    db_dump: Dict[str, str]
    include_audio: bool
    audio_plan: _AudioPlan = field(default_factory=_AudioPlan)
    document_plan: _DocumentPlan = field(default_factory=_DocumentPlan)
    archive_quality: str = ARCHIVE_QUALITY_COMPRESSED
    progress_callback: Any = None

    def report(self, stage: str, current: int = 0, total: int = 0) -> None:
        if self.progress_callback is None:
            return
        try:
            self.progress_callback(stage, current, total)
        except Exception:  # noqa: BLE001 -- reporting must never fail an export
            logger.debug("Backup progress callback failed", exc_info=True)


def _create_backup_sync(request: _ExportRequest) -> Tuple[str, Dict[str, Any]]:
    """
    Synchronous method to handle heavy file compression and zipping.
    Runs in a thread to prevent blocking the main event loop.

    Reports progress as it goes. Compressing a large library to Opus takes minutes,
    and without per-file reporting the export looks indistinguishable from a hang.

    Returns the temporary zip path and a warnings summary for the operator.
    """
    config_path = request.config_path
    db_dump = request.db_dump
    include_audio = request.include_audio
    audio_plan = request.audio_plan
    document_plan = request.document_plan
    archive_quality = request.archive_quality
    report = request.report

    # Written to the shared export directory so the API can serve it and the periodic
    # sweep can reclaim it.
    os.makedirs(BACKUP_EXPORT_DIR, exist_ok=True)
    temp_zip = tempfile.NamedTemporaryFile(
        delete=False, suffix=".zip", dir=BACKUP_EXPORT_DIR
    )
    temp_zip.close()

    failed_audio = 0
    failed_documents = 0

    try:
        with zipfile.ZipFile(temp_zip.name, "w", zipfile.ZIP_DEFLATED) as zipf:
            # 1. Write DB Dump
            report("Writing records")
            for filename, content in db_dump.items():
                zipf.writestr(filename, content)

            # 2. Add audio, one member per recording row.
            if include_audio:
                failed_audio = _write_audio_members(
                    zipf, audio_plan, archive_quality, report
                )

            # 3. Add attached documents, always included.
            failed_documents = _write_document_members(zipf, document_plan, report)

            # 4. Add Config
            report("Finalising archive")
            _write_config_member(zipf, config_path)

            # 5. Add Backup Info
            warnings = {
                "recordings_without_audio": audio_plan.missing_audio
                if include_audio
                else 0,
                "recordings_audio_failed": failed_audio,
                "documents_without_files": document_plan.missing_files,
                "documents_failed": failed_documents,
            }
            backup_info = {
                "format_version": BACKUP_FORMAT_VERSION,
                "version": runtime.get_app_version(),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "include_audio": include_audio,
                "archive_quality": archive_quality,
                "contains_restorable_calendar_credentials": True,
                "warnings": warnings,
            }
            zipf.writestr("backup_info.json", json.dumps(backup_info, indent=2))

        return temp_zip.name, warnings

    except Exception as e:
        # Cleanup temp zip if failed
        if os.path.exists(temp_zip.name):
            os.remove(temp_zip.name)
        raise e


async def create_backup(
    include_audio: bool = True,
    archive_quality: str = ARCHIVE_QUALITY_COMPRESSED,
    progress_callback: Any = None,
) -> Tuple[str, Dict[str, Any]]:
    path_manager = runtime.PathManager()
    recordings_dir = path_manager.recordings_directory
    documents_dir = runtime.documents_directory(path_manager)
    config_path = path_manager.config_path
    archive_quality = _normalise_archive_quality(archive_quality)

    import asyncio

    # 1. Dump Database (Async)
    db_dump = {}
    audio_plan = _AudioPlan()
    document_plan = _DocumentPlan()
    async with runtime.async_session_maker() as session:
        for table_index, (table_name, model_cls) in enumerate(runtime.MODELS, start=1):
            if progress_callback is not None:
                try:
                    progress_callback(
                        "Reading database", table_index, len(runtime.MODELS)
                    )
                except Exception:  # noqa: BLE001
                    logger.debug("Backup progress callback failed", exc_info=True)
            results = await session.execute(
                _table_dump_statement(table_name, model_cls)
            )
            items = results.scalars().all()

            if table_name == "recordings" and include_audio:
                audio_plan = _build_audio_plan(
                    list(items), recordings_dir, archive_quality
                )
            elif table_name == "documents":
                document_plan = _build_document_plan(list(items), documents_dir)

            data = _serialise_backup_table_rows(
                table_name, items, audio_plan, document_plan
            )

            db_dump[f"{table_name}.json"] = json.dumps(data, indent=2)

    # 2. Heavy Lifting in Thread
    return await asyncio.to_thread(
        _create_backup_sync,
        _ExportRequest(
            recordings_dir=recordings_dir,
            config_path=config_path,
            db_dump=db_dump,
            include_audio=include_audio,
            audio_plan=audio_plan,
            document_plan=document_plan,
            archive_quality=archive_quality,
            progress_callback=progress_callback,
        ),
    )


def create_backup_blocking(
    include_audio: bool = True,
    archive_quality: str = ARCHIVE_QUALITY_COMPRESSED,
    progress_callback: Any = None,
) -> Tuple[str, Dict[str, Any]]:
    path_manager = runtime.PathManager()
    recordings_dir = path_manager.recordings_directory
    documents_dir = runtime.documents_directory(path_manager)
    config_path = path_manager.config_path
    archive_quality = _normalise_archive_quality(archive_quality)

    db_dump: Dict[str, str] = {}
    audio_plan = _AudioPlan()
    document_plan = _DocumentPlan()
    with Session(runtime.sync_engine) as session:
        for table_index, (table_name, model_cls) in enumerate(runtime.MODELS, start=1):
            if progress_callback is not None:
                try:
                    progress_callback(
                        "Reading database", table_index, len(runtime.MODELS)
                    )
                except Exception:  # noqa: BLE001
                    logger.debug("Backup progress callback failed", exc_info=True)
            items = session.exec(_table_dump_statement(table_name, model_cls)).all()

            if table_name == "recordings" and include_audio:
                audio_plan = _build_audio_plan(
                    list(items), recordings_dir, archive_quality
                )
            elif table_name == "documents":
                document_plan = _build_document_plan(list(items), documents_dir)

            data = _serialise_backup_table_rows(
                table_name, items, audio_plan, document_plan
            )
            db_dump[f"{table_name}.json"] = json.dumps(data, indent=2)

    return _create_backup_sync(
        _ExportRequest(
            recordings_dir=recordings_dir,
            config_path=config_path,
            db_dump=db_dump,
            include_audio=include_audio,
            audio_plan=audio_plan,
            document_plan=document_plan,
            archive_quality=archive_quality,
            progress_callback=progress_callback,
        )
    )
