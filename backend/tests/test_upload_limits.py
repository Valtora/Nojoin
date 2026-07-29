import io

import pytest
from fastapi import HTTPException, UploadFile

from backend.utils.rate_limit import (
    acquire_concurrency_limit,
    enforce_upload_concurrency,
    release_concurrency_limit,
)
from backend.utils.upload_limit import (
    UPLOAD_LIMIT_BACKUP,
    UPLOAD_LIMIT_DOCUMENT,
    UPLOAD_LIMIT_LEGACY_RECORDING,
    UPLOAD_LIMIT_SEGMENT,
    stream_and_validate_upload,
)


@pytest.mark.anyio
async def test_stream_and_validate_upload_success(tmp_path):
    dest = tmp_path / "dest.txt"
    file_like = io.BytesIO(b"hello world")
    file = UploadFile(
        file=file_like, filename="test.txt", headers={"content-length": "11"}
    )

    bytes_written = await stream_and_validate_upload(file, str(dest), max_size=20)
    assert bytes_written == 11
    assert dest.read_bytes() == b"hello world"


@pytest.mark.anyio
async def test_stream_and_validate_upload_early_reject(tmp_path):
    dest = tmp_path / "dest.txt"
    file_like = io.BytesIO(b"hello world")
    file = UploadFile(
        file=file_like, filename="test.txt", headers={"content-length": "100"}
    )

    with pytest.raises(HTTPException) as exc_info:
        await stream_and_validate_upload(file, str(dest), max_size=10)
    assert exc_info.value.status_code == 413
    assert "exceeds" in exc_info.value.detail
    assert not dest.exists()


@pytest.mark.anyio
async def test_stream_and_validate_upload_chunk_reject(tmp_path):
    dest = tmp_path / "dest.txt"
    file_like = io.BytesIO(b"hello world")
    # No content-length header
    file = UploadFile(file=file_like, filename="test.txt")

    with pytest.raises(HTTPException) as exc_info:
        await stream_and_validate_upload(file, str(dest), max_size=5, chunk_size=2)
    assert exc_info.value.status_code == 413
    assert "exceeds" in exc_info.value.detail
    assert not dest.exists()


@pytest.mark.anyio
async def test_stream_and_validate_upload_interrupted_body_leaves_no_file(tmp_path):
    dest = tmp_path / "dest.webm"

    class InterruptedStream(io.RawIOBase):
        def __init__(self) -> None:
            self.reads = 0

        def read(self, size=-1):
            self.reads += 1
            if self.reads == 1:
                return b"partial-bytes"
            raise ConnectionError("client disconnected mid-body")

    file = UploadFile(file=InterruptedStream(), filename="dest.webm")

    with pytest.raises(ConnectionError):
        await stream_and_validate_upload(file, str(dest), max_size=1024, chunk_size=4)

    # A truncated upload must never become visible at the destination path,
    # and the temporary file must be cleaned up.
    assert not dest.exists()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.anyio
async def test_stream_and_validate_upload_replaces_existing_destination(tmp_path):
    dest = tmp_path / "dest.txt"
    dest.write_bytes(b"old contents")
    file = UploadFile(file=io.BytesIO(b"new contents"), filename="dest.txt")

    bytes_written = await stream_and_validate_upload(file, str(dest), max_size=64)

    assert bytes_written == 12
    assert dest.read_bytes() == b"new contents"
    assert list(tmp_path.iterdir()) == [dest]


@pytest.mark.anyio
async def test_concurrency_limiting_in_memory():
    key = "test_key_concurrency_limiting"
    # Reset state to clean
    await release_concurrency_limit(key)

    assert await acquire_concurrency_limit(key, limit=2) is True
    assert await acquire_concurrency_limit(key, limit=2) is True
    # Acquire 3 -> fails
    assert await acquire_concurrency_limit(key, limit=2) is False

    # Release one
    await release_concurrency_limit(key)
    # Acquire again -> succeeds
    assert await acquire_concurrency_limit(key, limit=2) is True

    # Cleanup
    await release_concurrency_limit(key)
    await release_concurrency_limit(key)


@pytest.mark.anyio
async def test_concurrency_context_manager():
    class DummyRequest:
        headers = {}
        client = None

    req = DummyRequest()
    user_id = "user_test_123"

    # Use context manager
    async with enforce_upload_concurrency(req, "test_ns", user_id, limit=1):
        # Nested call should fail
        with pytest.raises(HTTPException) as exc_info:
            async with enforce_upload_concurrency(req, "test_ns", user_id, limit=1):
                pass
        assert exc_info.value.status_code == 429

    # After context exits, we should be able to acquire again
    async with enforce_upload_concurrency(req, "test_ns", user_id, limit=1):
        pass


def test_upload_limit_constants():
    # Make sure defaults are set
    assert UPLOAD_LIMIT_SEGMENT == 15 * 1024 * 1024
    assert UPLOAD_LIMIT_LEGACY_RECORDING == 250 * 1024 * 1024
    assert UPLOAD_LIMIT_DOCUMENT == 20 * 1024 * 1024
    # Deliberately generous: an Original-quality archive stores audio without
    # re-encoding, so a backup this server produces can be far larger than a compressed
    # one, and a tighter cap would make our own exports non-restorable.
    assert UPLOAD_LIMIT_BACKUP == 25 * 1024 * 1024 * 1024
