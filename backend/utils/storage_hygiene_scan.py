"""The two halves a storage hygiene run subtracts: what the directories hold and
what the rows name.

A listed file is named by a row when any of its keys matches one of the row's:

- its device and inode, as :func:`document_file_identity` gives them, so a
  symlink, a hard link or another spelling of the same file counts;
- its name, compared without case, so a stored path that resolves elsewhere
  (another working directory, the backup's fallback resolver, a filesystem that
  reports two inode numbers for one file) still keeps every file of that name;
- for a recording still uploading or paused, its name up to the first dot, so
  the ``.flac`` master that finalize writes beside the ``.wav`` the row names,
  and its ``.part``, are kept even though no row names them yet.

Keys only ever keep more files; none of them can make a file a candidate.
"""

from __future__ import annotations

import logging
import os
import re
import stat
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import CompoundSelect, String, case, literal, select, union_all
from sqlmodel import Session, col

from backend.models.document import Document
from backend.models.recording import Recording, RecordingStatus
from backend.utils.document_storage import document_file_identity, documents_root_dir
from backend.utils.path_manager import PathManager
from backend.utils.recording_storage import recordings_root_dir
from backend.utils.storage_hygiene_report import (
    AUDIO,
    DOCUMENTS,
    PARTIAL_UPLOADS,
    PROXIES,
    UNRECOGNISED,
)

logger = logging.getLogger("backend.utils.storage_hygiene")

RECORDINGS_ROLE = "recordings"

# What a stored path counts toward, by the state of its row.
COUNTED = "counted"
PENDING = "pending"
SETTLED = "settled"
# A recording in these states has no audio file of its own yet, by design:
# capture init names a ``.wav`` that finalize replaces with the ``.flac`` master,
# and an upload's bytes are still arriving.
PENDING_STATUSES = (RecordingStatus.UPLOADING, RecordingStatus.PAUSED)
# A recording in these states may have settled without its file, by design: an
# import that fails removes its audio and keeps the row, and a capture whose
# master could not be built keeps naming the ``.wav`` it never got. Their paths
# still keep the files they name, but a missing one says nothing about whether
# the database belongs to this data directory. Every other status names a file
# that should be there.
SETTLED_STATUSES = (RecordingStatus.ERROR, RecordingStatus.CANCELLED)

_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_EXTENSION = r"(?:\.[A-Za-z0-9]{1,16})?"
# The names Nojoin gives what it stores: ``<uuid4><ext>`` for uploads, imports,
# captures, proxies and documents; ``<stem>__<uuid4><ext>``, possibly stacked,
# for a restored file whose name was taken; ``demo_welcome_<user id>.wav`` for the
# demo seed; and ``<name>.restored.wav`` for audio rebuilt from its proxy. A file
# under any other name, a restored one keeping another install's name included,
# is reported and never removed.
_STORED_NAME = re.compile(
    rf"^(?:{_UUID}|.+__{_UUID}|demo_welcome_[0-9]+)(?:\.restored)?{_EXTENSION}$"
)
# What a killed write leaves: upload_limit.py's ``<dest>.<32 hex>.upload-tmp``,
# audio.py's in-place proxy conversion ``<dest>.<8 hex>.tmp``, and
# browser_capture_audio.py's ``<dest>.part``.
_PARTIAL_WRITES = (
    re.compile(r"^(?P<dest>.+)\.[0-9a-f]{32}\.upload-tmp$"),
    re.compile(r"^(?P<dest>.+)\.[0-9a-f]{8}\.tmp$"),
    re.compile(r"^(?P<dest>.+)\.part$"),
)


def _partial_destination(name: str) -> str | None:
    """The name a partial write was headed for, or None for any other file."""
    for pattern in _PARTIAL_WRITES:
        match = pattern.match(name)
        if match is not None:
            return match.group("dest")
    return None


def is_partial_upload(name: str) -> bool:
    return _partial_destination(name) is not None


def is_nojoin_name(name: str) -> bool:
    """Whether Nojoin could have written a file of this name."""
    destination = _partial_destination(name)
    return bool(_STORED_NAME.match(name if destination is None else destination))


def _base_name(name: str) -> str:
    return name.split(".", 1)[0].casefold()


def _stored_name(raw_path: str) -> str:
    return re.split(r"[\\/]", raw_path.rstrip("\\/"))[-1]


@dataclass(frozen=True)
class ScanDirectory:
    role: str
    path: Path


@dataclass(frozen=True)
class StoredFile:
    path: Path
    directory: Path
    role: str
    device: int
    inode: int
    size: int
    mtime_ns: int

    def keys(self) -> tuple[tuple[object, ...], ...]:
        name = self.path.name
        return (
            ("inode", self.device, self.inode),
            ("name", name.casefold()),
            ("pending", _base_name(name)),
        )


@dataclass
class References:
    """What the rows name, and how many of their files are missing."""

    recordings: int = 0
    keys: dict[tuple[object, ...], str] = field(default_factory=dict)
    stored: dict[str, int] = field(default_factory=dict)
    missing: dict[str, int] = field(default_factory=dict)
    relative_stored: int = 0
    relative_missing: int = 0

    def category_of(self, stored: StoredFile) -> str | None:
        """The column that names this file, or None when no row does."""
        for key in stored.keys():
            if key in self.keys:
                return self.keys[key]
        return None

    def _name(self, key: tuple[object, ...], category: str) -> None:
        # Audio wins, so an MP3 that is its own proxy is reported as audio.
        if category == AUDIO or key not in self.keys:
            self.keys[key] = category

    def _count(self, category: str, raw_path: str, missing: bool) -> None:
        self.stored[category] = self.stored.get(category, 0) + 1
        self.missing[category] = self.missing.get(category, 0) + int(missing)
        if not raw_path.startswith(("/", "~", "\\")) and not re.match(
            r"^[A-Za-z]:", raw_path
        ):
            self.relative_stored += 1
            self.relative_missing += int(missing)

    def add(self, category: str, raw_path: str, state: str) -> None:
        """Add a stored path's keys, and count it unless its row is pending or
        settled without its file."""
        name = _stored_name(raw_path)
        self._name(("name", name.casefold()), category)
        identity = document_file_identity(raw_path)
        if identity is not None and identity[0] == "inode":
            self._name(identity, category)
        if state == PENDING:
            self._name(("pending", _base_name(name)), category)
        if state == COUNTED:
            self._count(category, raw_path, identity is None or identity[0] == "path")


def hygiene_directories() -> list[ScanDirectory]:
    """The documents directory and both recordings roots, resolved, each once.

    Uploads, imports and deletes use ``recordings_root_dir()``, which follows
    ``RECORDINGS_DIR`` and the working directory. The restore and the demo seed
    use ``PathManager().recordings_directory``, which follows the data directory.
    They differ whenever either moves, so both are scanned.
    """
    candidates = (
        (DOCUMENTS, documents_root_dir()),
        (RECORDINGS_ROLE, recordings_root_dir(create=False)),
        (RECORDINGS_ROLE, PathManager().recordings_directory),
    )
    directories: list[ScanDirectory] = []
    seen: set[Path] = set()
    for role, path in candidates:
        resolved = path.expanduser().resolve()
        if resolved not in seen:
            seen.add(resolved)
            directories.append(ScanDirectory(role=role, path=resolved))
    return directories


def _list_directory(directory: ScanDirectory) -> list[StoredFile]:
    try:
        names = os.listdir(directory.path)
    except FileNotFoundError:
        return []
    except OSError as error:
        logger.warning("Storage hygiene could not list %s: %s", directory.path, error)
        return []

    files: list[StoredFile] = []
    for name in names:
        if name.startswith("."):
            continue
        path = directory.path / name
        try:
            status = os.lstat(path)
        except OSError:
            continue
        if not stat.S_ISREG(status.st_mode):
            continue
        files.append(
            StoredFile(
                path=path,
                directory=directory.path,
                role=directory.role,
                device=status.st_dev,
                inode=status.st_ino,
                size=status.st_size,
                mtime_ns=status.st_mtime_ns,
            )
        )
    return files


def list_directories(directories: Sequence[ScanDirectory]) -> list[StoredFile]:
    files: list[StoredFile] = []
    for directory in directories:
        files.extend(_list_directory(directory))
    return files


def unreferenced_category(stored: StoredFile, siblings: set[tuple[Path, str]]) -> str:
    """The category of a file no row names, from its name and directory.

    A ``.mp3`` is a proxy when a file of the same base name and another
    extension sits beside it, and audio otherwise, as an MP3 import is.
    """
    name = stored.path.name
    if not is_nojoin_name(name):
        return UNRECOGNISED
    if is_partial_upload(name):
        return PARTIAL_UPLOADS
    if stored.role == DOCUMENTS:
        return DOCUMENTS
    is_mp3 = name.casefold().endswith(".mp3")
    if is_mp3 and (stored.directory, _base_name(name)) in siblings:
        return PROXIES
    return AUDIO


def non_mp3_bases(files: Sequence[StoredFile]) -> set[tuple[Path, str]]:
    return {
        (stored.directory, _base_name(stored.path.name))
        for stored in files
        if not stored.path.name.casefold().endswith(".mp3")
    }


def reference_statement() -> CompoundSelect:
    """Every stored path, soft-deleted rows included, in one statement.

    One statement is one snapshot of the database, even under READ COMMITTED.
    A proxy that is its recording's audio is read once, as the audio. Documents
    are read whether or not their recording exists.
    """
    status = col(Recording.status)
    state = case(
        (status.in_(PENDING_STATUSES), literal(PENDING, String)),
        (status.in_(SETTLED_STATUSES), literal(SETTLED, String)),
        else_=literal(COUNTED, String),
    )
    return union_all(
        select(literal(AUDIO, String), col(Recording.audio_path), state),
        select(literal(PROXIES, String), col(Recording.proxy_path), state).where(
            col(Recording.proxy_path).is_not(None),
            col(Recording.proxy_path) != col(Recording.audio_path),
        ),
        select(
            literal(DOCUMENTS, String),
            col(Document.file_path),
            literal(COUNTED, String),
        ),
    )


def read_references(session_factory: Callable[[], Session]) -> References:
    with session_factory() as session:
        rows = session.connection().execute(reference_statement()).all()

    references = References()
    for category, raw_path, state in rows:
        if category == AUDIO:
            references.recordings += 1
        if raw_path:
            references.add(category, raw_path, state)
    return references
