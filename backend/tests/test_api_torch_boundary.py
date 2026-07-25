"""The API process must never import torch.

The API image is built without torch or pyannote on purpose: heavy ML belongs in
the worker, and the API stays small and fast to start. Nothing enforced that,
so a lazy `from backend.processing.embedding_core import ...` inside a request
handler passed every test (the test environment has torch) and then raised
ModuleNotFoundError in production.

These tests run in a subprocess with torch blocked, which is what the API image
actually looks like.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap

import pytest

# Modules that sit on a request path and must therefore stay torch-free, along
# with the calls that previously reached torch lazily rather than at import.
_BLOCKER = """
import sys

class _Blocked:
    def find_module(self, name, path=None):
        return self if name == "torch" or name.startswith("torch.") else None

    def find_spec(self, name, path=None, target=None):
        if name == "torch" or name.startswith("torch."):
            raise ModuleNotFoundError("No module named 'torch'")
        return None

sys.meta_path.insert(0, _Blocked())
"""


def _run_without_torch(body: str) -> subprocess.CompletedProcess:
    script = _BLOCKER + textwrap.dedent(body) + "\nprint('TORCH_FREE_OK')\n"
    return subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=180,
    )


def _assert_torch_free(body: str) -> None:
    result = _run_without_torch(body)
    if "TORCH_FREE_OK" not in result.stdout:
        pytest.fail(
            "Import or call reached torch, which the API image does not ship.\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


def test_blocker_actually_blocks_torch():
    """Guard the guard: a broken blocker would make every test below vacuous."""
    result = _run_without_torch("import torch")
    assert "TORCH_FREE_OK" not in result.stdout
    assert "No module named 'torch'" in result.stderr


def test_embedding_module_is_importable_without_torch():
    # backend.processing.embedding is imported by several speaker endpoints.
    _assert_torch_free("import backend.processing.embedding")


def test_embedding_version_helpers_do_not_reach_torch_when_called():
    """Import-time cleanliness is not enough; the lazy imports matter too."""
    _assert_torch_free(
        """
        from backend.processing.embedding import (
            embedding_version_of,
            embeddings_are_comparable,
            find_matching_global_speaker,
        )

        class Row:
            def __init__(self, version, name="Alice", embedding=None):
                self.embedding_version = version
                self.name = name
                self.embedding = embedding

        assert embedding_version_of(Row(None)) == 1
        assert embeddings_are_comparable(Row(2), Row(2)) is True
        assert embeddings_are_comparable(Row(1), Row(2)) is False

        # The matching path resolves the current method version lazily.
        match, score = find_matching_global_speaker(
            [1.0, 0.0], [Row(2, embedding=[1.0, 0.0])]
        )
        assert match is not None
        """
    )


def test_speaker_cap_and_version_modules_are_torch_free():
    _assert_torch_free(
        """
        from backend.processing.speaker_cap import normalize_speaker_cap
        from backend.processing.embedding_version import (
            EMBEDDING_METHOD_VERSION,
            LEGACY_EMBEDDING_METHOD_VERSION,
        )

        assert normalize_speaker_cap("3") == 3
        assert EMBEDDING_METHOD_VERSION != LEGACY_EMBEDDING_METHOD_VERSION
        """
    )


def test_speakers_endpoints_import_without_torch():
    # This is the module that shipped the regression.
    _assert_torch_free("import backend.api.v1.endpoints.speakers.routes_voiceprint")


def test_recordings_endpoints_import_without_torch():
    _assert_torch_free(
        """
        import backend.api.v1.endpoints.recordings.routes_capture
        import backend.api.v1.endpoints.recordings.routes_import_upload
        import backend.api.v1.endpoints.recordings.routes_actions
        """
    )


def test_no_api_module_imports_embedding_core() -> None:
    """Catch the exact shape of the regression: a lazy import in a handler.

    ``backend.processing.embedding_core`` imports torch at module scope. A
    handler that imports it inside the function body passes import-time checks
    and only fails when the endpoint is actually called, which is how this
    reached production. Version constants live in
    ``backend.processing.embedding_version``, which is torch-free.
    """
    from pathlib import Path

    # Derived from this file's location rather than the package's __file__:
    # backend.api is a namespace package, so __file__ is None.
    backend_root = Path(__file__).resolve().parents[1]
    api_root = backend_root / "api"
    assert api_root.is_dir(), f"Expected the API package at {api_root}"

    offenders = [
        str(path.relative_to(backend_root))
        for path in sorted(api_root.rglob("*.py"))
        if "embedding_core" in path.read_text(encoding="utf-8")
    ]

    assert not offenders, (
        "These API modules reference embedding_core, which pulls in torch and "
        f"is absent from the API image: {offenders}. "
        "Import from backend.processing.embedding_version instead."
    )
