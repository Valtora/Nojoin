"""The import picker and the import API accept the same file extensions.

The web client filters its file picker with its own copy of the list, so an
extension added on one side only is either hidden from users or refused after
the upload.
"""

from __future__ import annotations

import re
from pathlib import Path

from backend.api.v1.endpoints.recordings.routes_import_upload import (
    SUPPORTED_AUDIO_FORMATS,
)

_FRONTEND_API = (
    Path(__file__).resolve().parents[2] / "frontend/src/lib/api/recordings.ts"
)


def _frontend_formats() -> set[str]:
    source = _FRONTEND_API.read_text(encoding="utf-8")
    body = re.search(
        r"export const getSupportedAudioFormats = \(\): string\[\] => \{(.*?)\n\};",
        source,
        re.DOTALL,
    )
    assert body, f"getSupportedAudioFormats not found in {_FRONTEND_API}"
    return set(re.findall(r'"(\.[a-z0-9]+)"', body.group(1)))


def test_picker_and_api_accept_the_same_extensions() -> None:
    assert _frontend_formats() == set(SUPPORTED_AUDIO_FORMATS)
