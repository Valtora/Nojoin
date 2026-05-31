import io
import os
import pytest
from fastapi import HTTPException, UploadFile, Request
from backend.utils.upload_limit import (
    stream_and_validate_upload,
    UPLOAD_LIMIT_SEGMENT,
    UPLOAD_LIMIT_LEGACY_RECORDING,
    UPLOAD_LIMIT_DOCUMENT,
    UPLOAD_LIMIT_BACKUP,
)
from backend.utils.rate_limit import (
    acquire_concurrency_limit,
    release_concurrency_limit,
    enforce_upload_concurrency,
)


@pytest.mark.anyio
async def test_stream_and_validate_upload_success(tmp_path):
    dest = tmp_path / "dest.txt"
    file_like = io.BytesIO(b"hello world")
    file = UploadFile(file=file_like, filename="test.txt", headers={"content-length": "11"})
    
    bytes_written = await stream_and_validate_upload(file, str(dest), max_size=20)
    assert bytes_written == 11
    assert dest.read_bytes() == b"hello world"


@pytest.mark.anyio
async def test_stream_and_validate_upload_early_reject(tmp_path):
    dest = tmp_path / "dest.txt"
    file_like = io.BytesIO(b"hello world")
    file = UploadFile(file=file_like, filename="test.txt", headers={"content-length": "100"})
    
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
async def test_concurrency_limiting_in_memory():
    key = "test_key_concurrency_limiting"
    # Reset state to clean
    await release_concurrency_limit(key)

    # Acquire 1
    assert await acquire_concurrency_limit(key, limit=2) is True
    # Acquire 2
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
    assert UPLOAD_LIMIT_BACKUP == 300 * 1024 * 1024
