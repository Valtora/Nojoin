import logging
import os
import uuid

import aiofiles
from fastapi import HTTPException, UploadFile

logger = logging.getLogger(__name__)

# Size limit configuration (defaults in bytes)
UPLOAD_LIMIT_SEGMENT = int(os.getenv("UPLOAD_LIMIT_SEGMENT", 15 * 1024 * 1024))
UPLOAD_LIMIT_LEGACY_RECORDING = int(
    os.getenv("UPLOAD_LIMIT_LEGACY_RECORDING", 250 * 1024 * 1024)
)
# Generous enough for a scanned report or a large deck, since visual parsing is
# now page-by-page with no page cap and the old 20 MB ceiling rejected files the
# parser handles fine. Matched to the legacy-recording limit rather than removed
# outright: this is the only user-driven write-to-disk path in the product, and
# the free-disk pre-flight below is the second line of defence.
UPLOAD_LIMIT_DOCUMENT = int(os.getenv("UPLOAD_LIMIT_DOCUMENT", 250 * 1024 * 1024))

# Above this, the client warns about parse duration and provider cost before
# confirming. Not a limit -- purely the threshold for an informed choice.
DOCUMENT_SIZE_WARNING_BYTES = int(
    os.getenv("DOCUMENT_SIZE_WARNING_BYTES", 20 * 1024 * 1024)
)

# Free space that must remain after an upload. Without this a single mistyped
# upload could fill the volume and take Postgres down with it.
DOCUMENT_DISK_HEADROOM_BYTES = int(
    os.getenv("DOCUMENT_DISK_HEADROOM_BYTES", 2 * 1024 * 1024 * 1024)
)


def ensure_disk_headroom(
    destination_dir: str,
    incoming_size: int | None,
    *,
    headroom: int = DOCUMENT_DISK_HEADROOM_BYTES,
) -> None:
    """Refuse an upload that would not leave enough free disk behind it.

    Mirrors the pre-flight the restore path already performs. ``incoming_size``
    comes from Content-Length and may be absent; an unknown size still checks
    that the headroom exists today, which catches an already-full volume.
    """
    import shutil

    try:
        free = shutil.disk_usage(destination_dir).free
    except OSError:  # pragma: no cover - unreadable mount, do not block the upload
        return

    required = headroom + max(incoming_size or 0, 0)
    if free < required:
        raise HTTPException(
            status_code=507,
            detail=(
                "Not enough free disk space to store this document. "
                f"{free // (1024 * 1024)} MB available."
            ),
        )


# Generous by design: an Original-quality archive stores audio without re-encoding, so a
# backup this server produces itself can be far larger than a compressed one. A cap below
# that would make our own exports non-restorable through our own UI. The pre-flight
# free-disk check is the real second line of defence.
UPLOAD_LIMIT_BACKUP = int(os.getenv("UPLOAD_LIMIT_BACKUP", 25 * 1024 * 1024 * 1024))


async def stream_and_validate_upload(
    file: UploadFile,
    dest_path: str,
    max_size: int,
    chunk_size: int = 65536,
) -> int:
    """
    Streams an UploadFile to a local path in bounded chunks, checking size limits.
    If the size limit is exceeded (either via Content-Length or actual read bytes),
    the partial file is deleted and an HTTPException with status 413 is raised.

    The stream is written to a unique temporary file next to the destination and
    moved into place with os.replace() only after the body completes, so an
    interrupted upload never leaves a truncated file at dest_path.
    """
    # 1. Check Content-Length header if present
    content_length_str = file.headers.get("content-length")
    if content_length_str:
        try:
            content_length = int(content_length_str)
            if content_length > max_size:
                logger.warning(
                    f"Upload rejected early: Content-Length {content_length} exceeds limit {max_size}"
                )
                raise HTTPException(
                    status_code=413,
                    detail=f"Upload size exceeds the maximum limit of {max_size} bytes.",
                )
        except ValueError:
            pass

    size_so_far = 0
    # ".upload-tmp" keeps in-flight files invisible to every directory scan that
    # keys on known media suffixes or an integer-parseable stem.
    temp_path = f"{dest_path}.{uuid.uuid4().hex}.upload-tmp"
    try:
        async with aiofiles.open(temp_path, "wb") as out_file:
            while True:
                chunk = await file.read(chunk_size)
                if not chunk:
                    break
                size_so_far += len(chunk)
                if size_so_far > max_size:
                    logger.warning(
                        f"Upload rejected: Transmitted bytes exceeded limit {max_size}"
                    )
                    raise HTTPException(
                        status_code=413,
                        detail=f"Upload size exceeds the maximum limit of {max_size} bytes.",
                    )
                await out_file.write(chunk)
        os.replace(temp_path, dest_path)
    except Exception as e:
        # Clean up partial file on failure
        try:
            if os.path.exists(temp_path):
                os.unlink(temp_path)
        except OSError as cleanup_err:
            logger.error(
                f"Failed to clean up partial upload file {temp_path}: {cleanup_err}"
            )
        raise e

    return size_so_far
