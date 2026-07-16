import uuid

import pytest

from backend.core.security import _migrate_legacy_secret_file
from backend.utils.path_manager import PathManager


def _reset_path_manager_singleton() -> None:
    PathManager._instance = None


def _manager_rooted_at(tmp_path) -> PathManager:
    _reset_path_manager_singleton()
    manager = PathManager.__new__(PathManager)
    manager._user_data_directory = tmp_path
    return manager


def test_get_upload_temp_dir_accepts_valid_uuid_and_contains_it(tmp_path):
    manager = _manager_rooted_at(tmp_path)
    base = tmp_path / "temp_uploads"

    # A canonical server-minted UUID round-trips to a directory inside the root.
    upload_id = str(uuid.uuid4())
    good = manager.get_upload_temp_dir(upload_id)
    assert good == base / upload_id
    assert good.is_relative_to(base)

    # The id is normalized through uuid.UUID, so casing/formatting variants map
    # to the same canonical directory rather than the raw request string.
    canonical = str(uuid.UUID(upload_id))
    assert manager.get_upload_temp_dir(upload_id.upper()) == base / canonical

    _reset_path_manager_singleton()


def test_get_upload_temp_dir_rejects_non_uuid_and_traversal_ids(tmp_path):
    manager = _manager_rooted_at(tmp_path)

    # Anything that is not a canonical UUID is refused outright: there is no
    # silent fallback bucket that two malformed uploads could collide inside,
    # and no request-derived string ever reaches the filesystem.
    hostile = [
        "../../etc/passwd",
        "a/../../b",
        "..%2f..",
        "....//x",
        "upload-123_abc",
        "../..",
        "",
    ]
    for bad in hostile:
        with pytest.raises(ValueError):
            manager.get_upload_temp_dir(bad)

    _reset_path_manager_singleton()


def test_get_chunk_path_contains_chunk_and_rejects_bad_index(tmp_path):
    manager = _manager_rooted_at(tmp_path)
    upload_id = str(uuid.uuid4())
    upload_dir = tmp_path / "temp_uploads" / upload_id

    # A valid index resolves to a .part file inside the upload dir.
    chunk_path = manager.get_chunk_path(upload_id, 0)
    assert chunk_path == upload_dir / "0.part"
    assert chunk_path.resolve().is_relative_to(upload_dir.resolve())

    # int coercion is the taint barrier: string-like ints are accepted and
    # normalised, non-numeric and negative values are refused outright.
    assert manager.get_chunk_path(upload_id, "12") == upload_dir / "12.part"
    for bad in (-1, "..", "0/../../etc/passwd", "1.part", "x"):
        with pytest.raises(ValueError):
            manager.get_chunk_path(upload_id, bad)

    # A hostile upload_id is still rejected via get_upload_temp_dir.
    with pytest.raises(ValueError):
        manager.get_chunk_path("../../etc/passwd", 0)

    _reset_path_manager_singleton()


def test_containerized_runtime_uses_persisted_project_data_dir(monkeypatch, tmp_path):
    project_root = tmp_path / "project"
    (project_root / "data").mkdir(parents=True)

    _reset_path_manager_singleton()
    monkeypatch.setattr(PathManager, "_get_project_root", lambda self: project_root)
    monkeypatch.setattr(PathManager, "_is_containerized_runtime", lambda self: True)

    manager = PathManager()

    assert manager.app_directory == project_root
    assert manager.executable_directory == project_root
    assert manager.user_data_directory == project_root / "data"

    _reset_path_manager_singleton()


def test_legacy_secret_key_is_migrated_into_persisted_data_dir(tmp_path):
    current_key_file = tmp_path / "data" / ".secret_key"
    legacy_key_file = tmp_path / "Documents" / "Nojoin" / ".secret_key"
    legacy_key_file.parent.mkdir(parents=True)
    current_key_file.parent.mkdir(parents=True)

    current_key_file.write_text("stale-current-key", encoding="utf-8")
    legacy_key_file.write_text("active-legacy-key", encoding="utf-8")

    _migrate_legacy_secret_file(current_key_file, legacy_key_file)

    assert current_key_file.read_text(encoding="utf-8") == "active-legacy-key"
    assert not legacy_key_file.exists()
    assert legacy_key_file.with_name(".secret_key.migrated").exists()
