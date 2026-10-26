"""Model-download response contract, mirrored in frontend/src/types/index.ts."""

from typing import Literal

from pydantic import BaseModel

ModelPreparationState = Literal["absent", "downloading", "ready", "failed"]


class ModelStatusInfo(BaseModel):
    downloaded: bool
    path: str | None
    checked_paths: list[str]
    source: Literal["bundled", "cache", "external"] | None = None
    partial: bool | None = None
    verification_state: Literal["missing", "partial", "verified", "invalid"] | None = (
        None
    )
    revision: str | None = None
    manifest_sha256: str | None = None
    verification_error: str | None = None
    state: ModelPreparationState | None = None
    size_bytes: int | None = None
    unused_reason: str | None = None
