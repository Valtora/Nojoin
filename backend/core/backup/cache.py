"""The most recent finished backup, kept so an unchanged library is not archived twice.

One archive is kept, with a manifest beside it in the export directory. A new export
replaces both, and the periodic sweep leaves them alone, so the archive stays available
until a newer one replaces it or an administrator deletes it. The manifest is written
after the archive is in place and before the previous archive is removed, so a reader
never sees a manifest that names a missing file.

The worker writes here and the API reads, which is why everything goes through files in
the shared export directory rather than through either process's memory.
"""

import json
import logging
import os
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from backend.core.backup.format import BACKUP_FORMAT_VERSION

logger = logging.getLogger(__name__)

MANIFEST_NAME = "latest-backup.json"


@dataclass
class CachedExport:
    export_id: str
    filename: str
    created_at: str
    include_audio: bool
    archive_quality: str
    fingerprint: str
    size_bytes: int
    warnings: Dict[str, int] = field(default_factory=dict)
    app_version: str = ""
    format_version: int = BACKUP_FORMAT_VERSION

    def matches(
        self, fingerprint: str, include_audio: bool, archive_quality: str
    ) -> bool:
        return (
            self.fingerprint == fingerprint
            and self.include_audio == include_audio
            and self.archive_quality == archive_quality
        )

    def summary(self) -> Dict[str, Any]:
        """What the browser is told. No paths and no fingerprint."""
        return {
            "export_id": self.export_id,
            "created_at": self.created_at,
            "include_audio": self.include_audio,
            "archive_quality": self.archive_quality,
            "size_bytes": self.size_bytes,
            "warnings": dict(self.warnings),
        }

    def download_filename(self) -> str:
        """Named for when its data was read, which may be days before the download."""
        try:
            stamp = datetime.fromisoformat(self.created_at)
        except ValueError:
            return "nojoin_backup.zip"
        return f"nojoin_backup_{stamp.strftime('%Y%m%d_%H%M%S')}.zip"


def _manifest_path(export_dir: Path) -> Path:
    return Path(export_dir) / MANIFEST_NAME


def archive_path(export_dir: Path, entry: CachedExport) -> Path:
    return Path(export_dir) / entry.filename


def load(export_dir: Path) -> Optional[CachedExport]:
    """Return the cached export, or None if there is none or its archive is gone."""
    manifest = _manifest_path(export_dir)
    try:
        data = json.loads(manifest.read_text())
        entry = CachedExport(**data)
    except FileNotFoundError:
        return None
    except (OSError, ValueError, TypeError) as e:
        logger.warning("Ignoring unreadable backup manifest %s: %s", manifest, e)
        return None

    # A manifest naming anything but a file in this directory was not written by us.
    if os.path.basename(entry.filename) != entry.filename:
        logger.warning("Ignoring backup manifest with an unexpected filename.")
        return None
    if not archive_path(export_dir, entry).is_file():
        return None
    return entry


def publish(export_dir: Path, built_zip: str, entry: CachedExport) -> CachedExport:
    """Make a freshly built archive the cached one, replacing whatever was there.

    ``entry`` describes the new archive. Its filename and size are filled in here, once
    the archive is in place.
    """
    export_dir = Path(export_dir)
    previous = load(export_dir)

    filename = f"nojoin-backup-{entry.export_id}.zip"
    final_path = export_dir / filename
    os.replace(built_zip, final_path)
    entry = replace(entry, filename=filename, size_bytes=final_path.stat().st_size)

    manifest = _manifest_path(export_dir)
    staging = manifest.with_name(f".{MANIFEST_NAME}.{entry.export_id}.tmp")
    staging.write_text(json.dumps(asdict(entry), indent=2))
    os.replace(staging, manifest)

    if previous is not None and previous.filename != filename:
        try:
            archive_path(export_dir, previous).unlink()
        except OSError as e:
            logger.warning(
                "Failed to remove the replaced backup %s: %s", previous.filename, e
            )

    return entry


def discard(export_dir: Path) -> bool:
    """Delete the cached archive and its manifest. Returns whether there was one."""
    entry = load(export_dir)
    removed = False
    if entry is not None:
        try:
            archive_path(export_dir, entry).unlink()
            removed = True
        except FileNotFoundError:
            pass
    try:
        _manifest_path(export_dir).unlink()
    except FileNotFoundError:
        pass
    return removed


def protected_names(export_dir: Path) -> set[str]:
    """Entries in the export directory the periodic sweep must keep."""
    entry = load(export_dir)
    names = {MANIFEST_NAME}
    if entry is not None:
        names.add(entry.filename)
    return names
