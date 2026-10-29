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

from backend.core.backup import cache as backup_cache
from backend.core.backup import runtime
from backend.core.backup.cache import CachedExport
from backend.core.backup.fingerprint import compute_fingerprint
from backend.core.backup.format import (
    ARCHIVE_QUALITIES,
    ARCHIVE_QUALITY_COMPRESSED,
    BACKUP_FORMAT_VERSION,
)
from backend.core.backup.live_transcripts import project_live_transcript_rows
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
from backend.utils.media_commands import FFMPEG

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
        *FFMPEG,
        "-y",
        "-i",
        input_path,
        # Defensive: states the audio-only intent explicitly. The .opus output
        # already selects ffmpeg's audio-only muxer, so a video import's picture
        # is not encoded either way.
        "-vn",
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


def _audio_member_compression(arcname: str) -> int:
    """How an audio member is stored in the zip, decided by its extension.

    A member whose extension is a compressed audio format is stored uncompressed:
    deflating it costs CPU for nothing (on Opus it measured a 0.99 ratio at about
    58 MB/s). WAV normally holds raw PCM, which deflate still shrinks. The file's
    contents are not inspected, so a WAV holding compressed audio is deflated for
    no gain, and another container carrying PCM (a MOV, AVI or MKA can) is stored
    at full size.
    """
    if arcname.lower().endswith(".wav"):
        return zipfile.ZIP_DEFLATED
    return zipfile.ZIP_STORED


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
            compress_type = _audio_member_compression(entry.arcname)
            if entry.compress:
                opus_path = _compress_to_opus(entry.source_path)
                zipf.write(opus_path, entry.arcname, compress_type=compress_type)
            else:
                zipf.write(
                    entry.source_path, entry.arcname, compress_type=compress_type
                )
        except Exception as e:  # noqa: BLE001 -- boundary: one audio file failing is counted; the archive still completes
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
        except Exception as e:  # noqa: BLE001 -- boundary: one document failing is counted; the archive still completes
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
    except Exception as e:  # noqa: BLE001 -- boundary: the config member is optional; the archive still completes
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
        except Exception:  # noqa: BLE001 -- boundary: reporting must never fail an export
            logger.debug("Backup progress callback failed", exc_info=True)


def _create_backup_sync(request: _ExportRequest) -> Tuple[str, Dict[str, Any]]:
    """
    Synchronous method to handle heavy file compression and zipping.
    Runs in a thread to prevent blocking the main event loop.

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
    export_dir = runtime.backup_export_directory(runtime.PathManager())
    os.makedirs(export_dir, exist_ok=True)
    temp_zip = tempfile.NamedTemporaryFile(delete=False, suffix=".zip", dir=export_dir)
    temp_zip.close()

    failed_audio = 0
    failed_documents = 0

    try:
        with zipfile.ZipFile(temp_zip.name, "w", zipfile.ZIP_DEFLATED) as zipf:
            report("Writing records")
            for filename, content in db_dump.items():
                zipf.writestr(filename, content)

            # Audio, one member per recording row.
            if include_audio:
                failed_audio = _write_audio_members(
                    zipf, audio_plan, archive_quality, report
                )

            # Attached documents, always included.
            failed_documents = _write_document_members(zipf, document_plan, report)

            report("Finalising archive")
            _write_config_member(zipf, config_path)

            warnings = {
                "recordings_without_audio": audio_plan.missing_audio
                if include_audio
                else 0,
                "recordings_audio_failed": failed_audio + audio_plan.colliding_audio,
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
        if os.path.exists(temp_zip.name):
            os.remove(temp_zip.name)
        raise e


def snapshot_session() -> Session:
    """A session whose reads all see the database as it was at its first query.

    PostgreSQL's default isolation gives each statement its own snapshot, so an export
    reading twenty tables one after another could archive a transcript whose recording
    was deleted between the two reads, or miss the recording a new transcript belongs to.
    REPEATABLE READ holds one snapshot for the whole transaction. The SQLite the tests use
    has no such level and needs none.
    """
    engine = runtime.sync_engine
    if engine.dialect.name == "postgresql":
        engine = engine.execution_options(isolation_level="REPEATABLE READ")
    return Session(engine)


@dataclass
class _Snapshot:
    """The database side of one export, read in a single transaction."""

    db_dump: Dict[str, str]
    audio_plan: _AudioPlan
    document_plan: _DocumentPlan
    fingerprint: str
    taken_at: str


def _report(
    progress_callback: Any, stage: str, current: int = 0, total: int = 0
) -> None:
    if progress_callback is None:
        return
    try:
        progress_callback(stage, current, total)
    except Exception:  # noqa: BLE001 -- boundary: reporting must never fail an export
        logger.debug("Backup progress callback failed", exc_info=True)


def _read_snapshot(
    include_audio: bool, archive_quality: str, progress_callback: Any = None
) -> _Snapshot:
    path_manager = runtime.PathManager()
    recordings_dir = path_manager.recordings_directory
    documents_dir = runtime.documents_directory(path_manager)

    db_dump: Dict[str, str] = {}
    audio_plan = _AudioPlan()
    document_plan = _DocumentPlan()
    with snapshot_session() as session:
        # First, so the fingerprint stored with the archive describes the rows below.
        fingerprint = compute_fingerprint(
            session,
            include_audio=include_audio,
            archive_quality=archive_quality,
            path_manager=path_manager,
        )
        taken_at = datetime.now(timezone.utc).isoformat()

        for table_index, (table_name, model_cls) in enumerate(runtime.MODELS, start=1):
            _report(
                progress_callback, "Reading database", table_index, len(runtime.MODELS)
            )
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
            if table_name == "transcripts":
                project_live_transcript_rows(session, data)
            db_dump[f"{table_name}.json"] = json.dumps(data, indent=2)

    return _Snapshot(
        db_dump=db_dump,
        audio_plan=audio_plan,
        document_plan=document_plan,
        fingerprint=fingerprint,
        taken_at=taken_at,
    )


def _build_from_snapshot(
    snapshot: _Snapshot,
    include_audio: bool,
    archive_quality: str,
    progress_callback: Any = None,
) -> Tuple[str, Dict[str, Any]]:
    path_manager = runtime.PathManager()
    return _create_backup_sync(
        _ExportRequest(
            recordings_dir=path_manager.recordings_directory,
            config_path=path_manager.config_path,
            db_dump=snapshot.db_dump,
            include_audio=include_audio,
            audio_plan=snapshot.audio_plan,
            document_plan=snapshot.document_plan,
            archive_quality=archive_quality,
            progress_callback=progress_callback,
        )
    )


def create_backup_blocking(
    include_audio: bool = True,
    archive_quality: str = ARCHIVE_QUALITY_COMPRESSED,
    progress_callback: Any = None,
) -> Tuple[str, Dict[str, Any]]:
    """Build a new archive and return its path, bypassing the cache."""
    archive_quality = _normalise_archive_quality(archive_quality)
    snapshot = _read_snapshot(include_audio, archive_quality, progress_callback)
    return _build_from_snapshot(
        snapshot, include_audio, archive_quality, progress_callback
    )


async def create_backup(
    include_audio: bool = True,
    archive_quality: str = ARCHIVE_QUALITY_COMPRESSED,
    progress_callback: Any = None,
) -> Tuple[str, Dict[str, Any]]:
    """The blocking export, run off the event loop.

    A wrapper rather than a second implementation, so the tests that call it exercise the
    code the worker runs.
    """
    import asyncio

    return await asyncio.to_thread(
        create_backup_blocking,
        include_audio=include_audio,
        archive_quality=archive_quality,
        progress_callback=progress_callback,
    )


def current_fingerprint(include_audio: bool, archive_quality: str) -> str:
    """Fingerprint the installation as it is now, for comparison with the cache."""
    with snapshot_session() as session:
        return compute_fingerprint(
            session,
            include_audio=include_audio,
            archive_quality=_normalise_archive_quality(archive_quality),
            path_manager=runtime.PathManager(),
        )


def find_current_export(
    include_audio: bool, archive_quality: str
) -> CachedExport | None:
    """Return the cached export if it has these options and nothing has changed since."""
    archive_quality = _normalise_archive_quality(archive_quality)
    cached = backup_cache.load(runtime.backup_export_directory(runtime.PathManager()))
    if cached is None:
        return None
    if (
        cached.include_audio != include_audio
        or cached.archive_quality != archive_quality
    ):
        return None
    if not cached.matches(
        current_fingerprint(include_audio, archive_quality),
        include_audio,
        archive_quality,
    ):
        return None
    return cached


def export_backup(
    *,
    export_id: str,
    include_audio: bool = True,
    archive_quality: str = ARCHIVE_QUALITY_COMPRESSED,
    progress_callback: Any = None,
) -> Tuple[CachedExport, bool]:
    """Return a current archive for these options, building one only if needed.

    Returns the cached export and whether it was reused. A build reads the database in one
    snapshot, writes the archive, and then replaces the cache with it, so the fingerprint
    stored beside the archive describes the data inside it even when the build takes
    hours and the library changes meanwhile.
    """
    archive_quality = _normalise_archive_quality(archive_quality)
    export_dir = runtime.backup_export_directory(runtime.PathManager())

    _report(progress_callback, "Checking for changes")
    cached = find_current_export(include_audio, archive_quality)
    if cached is not None:
        logger.info("Backup unchanged since export %s; reusing it.", cached.export_id)
        return cached, True

    snapshot = _read_snapshot(include_audio, archive_quality, progress_callback)
    zip_path, warnings = _build_from_snapshot(
        snapshot, include_audio, archive_quality, progress_callback
    )
    try:
        entry = backup_cache.publish(
            export_dir,
            zip_path,
            CachedExport(
                export_id=export_id,
                filename="",
                created_at=snapshot.taken_at,
                include_audio=include_audio,
                archive_quality=archive_quality,
                fingerprint=snapshot.fingerprint,
                size_bytes=0,
                warnings=warnings,
                app_version=runtime.get_app_version(),
            ),
        )
    except Exception:
        if os.path.exists(zip_path):
            os.remove(zip_path)
        raise
    return entry, False
