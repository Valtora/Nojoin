"""Stored file paths are read only from inside their own directories.

``backend/utils/stored_paths.py`` decides; these tests pin the decision and the
places that read a recording's audio, proxy or segments, or a document's file,
through it. Each test sets the directories itself, so ``elsewhere`` below is
outside all of them.
"""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.responses import FileResponse

from backend.api.v1.endpoints import documents as document_routes
from backend.api.v1.endpoints.recordings import routes_query
from backend.models.document import Document
from backend.models.recording import Recording, RecordingStatus
from backend.utils import stored_paths
from backend.utils.embedding_audio import select_recording_audio_for_embedding
from backend.utils.import_audio import KEEPING_AUDIO_STEP


@pytest.fixture
def dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """A recordings, a documents and an unrelated directory, the first two the roots."""
    found = SimpleNamespace(
        recordings=tmp_path / "recordings",
        documents=tmp_path / "documents",
        elsewhere=tmp_path / "elsewhere",
    )
    for directory in vars(found).values():
        directory.mkdir()
    monkeypatch.setattr(stored_paths, "recording_roots", lambda: (found.recordings,))
    monkeypatch.setattr(stored_paths, "document_roots", lambda: (found.documents,))
    return found


def _file(directory: Path, name: str, content: bytes = b"data") -> str:
    path = directory / name
    path.write_bytes(content)
    return str(path)


def test_a_path_is_taken_only_inside_its_directory(dirs: SimpleNamespace) -> None:
    inside = _file(dirs.recordings, "meeting.wav")
    outside = _file(dirs.elsewhere, "meeting.wav")
    linked = dirs.recordings / "linked.wav"
    linked.symlink_to(outside)
    climbing = f"{dirs.recordings}/../elsewhere/meeting.wav"

    assert stored_paths.confined_recording_path(inside) == inside
    assert stored_paths.confined_recording_path(outside) is None
    assert stored_paths.confined_recording_path(str(linked)) is None
    assert stored_paths.confined_recording_path(climbing) is None
    assert stored_paths.confined_recording_path(None) is None
    assert stored_paths.confined_recording_path("") is None
    # A file not yet written is judged by where it would be.
    assert stored_paths.confined_recording_path(str(dirs.recordings / "new.flac"))

    with pytest.raises(ValueError):
        stored_paths.require_recording_path(outside)
    assert stored_paths.require_recording_path(inside) == inside

    document = _file(dirs.documents, "agenda.pdf")
    assert stored_paths.confined_document_path(document) == document
    assert stored_paths.confined_document_path(inside) is None
    assert stored_paths.confined_document_path(_file(dirs.elsewhere, "a.pdf")) is None


def test_the_roots_are_both_recordings_directories_and_the_documents_directory(
    stored_files_may_live_in_temp, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_recording_roots, real_document_roots = stored_files_may_live_in_temp
    data_dir = tmp_path / "data"
    monkeypatch.setenv("RECORDINGS_DIR", str(tmp_path / "uploads"))
    monkeypatch.setattr(
        stored_paths,
        "PathManager",
        lambda: SimpleNamespace(
            recordings_directory=data_dir / "recordings",
            documents_directory=data_dir / "documents",
        ),
    )

    assert real_recording_roots() == (tmp_path / "uploads", data_dir / "recordings")
    assert real_document_roots() == (data_dir / "documents",)


# --- the API ----------------------------------------------------------------


def _recording(audio_path: str | None, proxy_path: str | None) -> SimpleNamespace:
    return SimpleNamespace(
        id=1, user_id=1, audio_path=audio_path, proxy_path=proxy_path
    )


async def _stream(monkeypatch: pytest.MonkeyPatch, recording: SimpleNamespace):
    async def owned(db, recording_id, user_id):
        return recording

    monkeypatch.setattr(routes_query, "_get_owned_recording", owned)
    return await routes_query.stream_recording(
        "rec-1",
        SimpleNamespace(headers={}),
        db=None,
        current_user=SimpleNamespace(id=1),
    )


@pytest.mark.anyio
async def test_stream_answers_404_for_a_proxy_outside_the_recordings_directories(
    dirs: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    recording = _recording(
        _file(dirs.elsewhere, "meeting.wav"), _file(dirs.elsewhere, "meeting.mp3")
    )

    with pytest.raises(HTTPException) as raised:
        await _stream(monkeypatch, recording)

    assert raised.value.status_code == 404
    assert "outside" not in raised.value.detail


@pytest.mark.anyio
async def test_stream_still_serves_a_proxy_inside_a_recordings_directory(
    dirs: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    recording = _recording(None, _file(dirs.recordings, "meeting.mp3", b"mp3"))

    response = await _stream(monkeypatch, recording)

    assert response.status_code == 200


@pytest.mark.anyio
async def test_info_reads_no_file_outside_the_recordings_directories(
    dirs: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    analysed: list[str] = []
    monkeypatch.setattr(
        "backend.processing.audio_preprocessing.analyze_audio_file",
        lambda path: analysed.append(path) or {"path": path},
    )

    async def owned(db, recording_id, user_id):
        return _recording(
            _file(dirs.elsewhere, "meeting.wav"), _file(dirs.recordings, "meeting.mp3")
        )

    monkeypatch.setattr(routes_query, "_get_owned_recording", owned)

    info = await routes_query.get_recording_info(
        "rec-1", db=None, current_user=SimpleNamespace(id=1)
    )

    assert info["original"] is None
    assert analysed == [str(dirs.recordings / "meeting.mp3")]


class _Db:
    def __init__(self, document: SimpleNamespace) -> None:
        self.rows = {
            Document: document,
            Recording: SimpleNamespace(id=7, user_id=1),
        }

    async def get(self, model, row_id):
        return self.rows.get(model)


def _document(file_path: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=3, recording_id=7, file_path=file_path, title="Agenda", file_type=None
    )


@pytest.mark.anyio
async def test_document_download_answers_404_for_a_file_outside_its_directory(
    dirs: SimpleNamespace,
) -> None:
    document = _document(_file(dirs.elsewhere, "agenda.pdf"))

    with pytest.raises(HTTPException) as raised:
        await document_routes.download_document(
            3, db=_Db(document), current_user=SimpleNamespace(id=1)
        )

    assert raised.value.status_code == 404
    assert raised.value.detail == "Document not found"


@pytest.mark.anyio
async def test_document_download_still_serves_its_own_file(
    dirs: SimpleNamespace,
) -> None:
    path = _file(dirs.documents, "agenda.pdf")

    response = await document_routes.download_document(
        3, db=_Db(_document(path)), current_user=SimpleNamespace(id=1)
    )

    assert isinstance(response, FileResponse)
    assert response.path == path


# --- the workers --------------------------------------------------------------


def test_voiceprints_read_no_audio_outside_the_recordings_directories(
    dirs: SimpleNamespace,
) -> None:
    recording = _recording(
        _file(dirs.elsewhere, "meeting.wav"), _file(dirs.elsewhere, "meeting.mp3")
    )

    assert select_recording_audio_for_embedding(recording) is None


def test_processing_makes_no_audio_from_a_proxy_outside_the_recordings_directories(
    dirs: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    from backend.worker.tasks import pipeline

    converted: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "backend.utils.audio.convert_to_wav",
        lambda source, target: converted.append((source, target)) or True,
    )
    proxy = _file(dirs.elsewhere, "meeting.mp3")
    recording = SimpleNamespace(
        audio_path=str(dirs.elsewhere / "meeting.webm"), proxy_path=proxy
    )
    session = SimpleNamespace(add=lambda row: None, commit=lambda: None)
    ctx = SimpleNamespace(session=session, device_suffix="", temp_files=[])

    with pytest.raises(FileNotFoundError):
        pipeline._resolve_input_audio(ctx, recording)

    assert converted == []
    assert sorted(os.listdir(dirs.elsewhere)) == ["meeting.mp3"]


def test_an_import_outside_the_recordings_directories_is_not_kept(
    dirs: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    from backend.worker.tasks import imported_audio

    source = _file(dirs.elsewhere, "meeting.mkv")
    recording = SimpleNamespace(
        status=RecordingStatus.QUEUED,
        processing_step=KEEPING_AUDIO_STEP,
        audio_path=source,
        proxy_path=None,
    )
    session = SimpleNamespace(
        get=lambda model, row_id: recording, rollback=lambda: None
    )
    failed: list[str] = []
    monkeypatch.setattr(
        imported_audio,
        "_fail",
        lambda session, recording_id, source, proxy_path, detail: failed.append(detail),
    )
    task = imported_audio.keep_imported_audio_task
    monkeypatch.setattr(task, "_session", session, raising=False)

    task.run(5)

    assert failed == [imported_audio.SERVER_FAILURE_DETAIL]
    assert Path(source).read_bytes() == b"data"


def test_a_document_outside_its_directory_is_not_parsed(
    dirs: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    from backend.worker.tasks import documents as document_tasks

    document = SimpleNamespace(
        id=3, file_path=_file(dirs.elsewhere, "agenda.pdf"), status=None
    )
    session = SimpleNamespace(
        get=lambda model, row_id: document, add=lambda row: None, commit=lambda: None
    )
    errors: list[Exception] = []
    monkeypatch.setattr(
        document_tasks,
        "_parse_document",
        lambda *args: pytest.fail("the document was read"),
    )
    monkeypatch.setattr(
        document_tasks,
        "_record_parse_failure",
        lambda session, document, document_id, error: errors.append(error),
    )
    task = document_tasks.process_document_task
    monkeypatch.setattr(task, "_session", session, raising=False)

    task.run(3)

    assert [type(error) for error in errors] == [FileNotFoundError]
