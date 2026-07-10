from backend.core.security import _migrate_legacy_secret_file
from backend.utils.path_manager import PathManager


def _reset_path_manager_singleton() -> None:
    PathManager._instance = None


def _manager_rooted_at(tmp_path) -> PathManager:
    _reset_path_manager_singleton()
    manager = PathManager.__new__(PathManager)
    manager._user_data_directory = tmp_path
    return manager


def test_get_upload_temp_dir_sanitizes_and_contains_upload_id(tmp_path):
    manager = _manager_rooted_at(tmp_path)
    base = (tmp_path / "temp_uploads").resolve()

    # Legitimate id round-trips unchanged and lands inside the uploads root.
    good = manager.get_upload_temp_dir("upload-123_abc")
    assert good == base / "upload-123_abc"
    assert good.is_relative_to(base)

    # Traversal payloads are stripped to safe segments that stay contained.
    for hostile in ["../../etc/passwd", "a/../../b", "..%2f..", "....//x"]:
        result = manager.get_upload_temp_dir(hostile)
        assert result.is_relative_to(base)

    _reset_path_manager_singleton()


def test_get_upload_temp_dir_falls_back_when_id_is_empty(tmp_path):
    manager = _manager_rooted_at(tmp_path)
    base = (tmp_path / "temp_uploads").resolve()

    # An id that sanitizes to nothing must not resolve to the base itself.
    result = manager.get_upload_temp_dir("../..")
    assert result == base / "default_upload"
    assert result.is_relative_to(base)

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
