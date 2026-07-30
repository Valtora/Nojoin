"""Shared types for sending images to a language model.

Lives in utils rather than in the documents package so the LLM backends can
depend on it without importing anything that pulls in PyMuPDF or python-pptx --
those are worker-only dependencies and the API image does not ship them.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Optional


class VisionUnsupportedError(RuntimeError):
    """The configured provider or model cannot accept images.

    Raised rather than returned so the caller can distinguish "this model has
    no vision" -- which downgrades the whole document to a structural parse and
    warns once -- from a per-page failure, which only marks that page.
    """


@dataclass(frozen=True)
class VisionImage:
    """One image bound for a model.

    Both representations are carried because the providers want different
    things and converting after the fact is wasteful: the HTTP APIs and the
    Claude Agent SDK take base64 inline, while ``codex exec`` takes ``--image
    <FILE>`` and reads the file itself. Rendered pages are written to a temp
    file regardless, so ``path`` is always available in practice.
    """

    data: bytes
    media_type: str
    path: Optional[str] = None

    def to_base64(self) -> str:
        return base64.b64encode(self.data).decode("ascii")
