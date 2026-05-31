import os
import logging
from fastapi import HTTPException, UploadFile
import aiofiles

logger = logging.getLogger(__name__)

# Size limit configuration (defaults in bytes)
UPLOAD_LIMIT_SEGMENT = int(os.getenv("UPLOAD_LIMIT_SEGMENT", 15 * 1024 * 1024))
UPLOAD_LIMIT_LEGACY_RECORDING = int(os.getenv("UPLOAD_LIMIT_LEGACY_RECORDING", 250 * 1024 * 1024))
UPLOAD_LIMIT_DOCUMENT = int(os.getenv("UPLOAD_LIMIT_DOCUMENT", 20 * 1024 * 1024))
UPLOAD_LIMIT_BACKUP = int(os.getenv("UPLOAD_LIMIT_BACKUP", 300 * 1024 * 1024))

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
                    detail=f"Upload size exceeds the maximum limit of {max_size} bytes."
                )
        except ValueError:
            pass

    size_so_far = 0
    try:
        async with aiofiles.open(dest_path, "wb") as out_file:
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
                        detail=f"Upload size exceeds the maximum limit of {max_size} bytes."
                    )
                await out_file.write(chunk)
    except Exception as e:
        # Clean up partial file on failure
        try:
            if os.path.exists(dest_path):
                os.unlink(dest_path)
        except OSError as cleanup_err:
            logger.error(f"Failed to clean up partial upload file {dest_path}: {cleanup_err}")
        raise e

    return size_so_far
