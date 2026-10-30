"""System response contracts, mirrored in frontend/src/types/index.ts."""

from datetime import datetime
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


class ResourceMemory(BaseModel):
    used_bytes: int
    total_bytes: int


class HostResources(BaseModel):
    cpu_percent: float
    memory: ResourceMemory


class NojoinResources(BaseModel):
    cpu_percent: float | None
    memory_bytes: int


class GpuResources(BaseModel):
    uuid: str
    name: str
    utilisation_percent: float | None
    memory: ResourceMemory


class ResourceTask(BaseModel):
    name: str
    started_at: datetime | None
    elapsed_seconds: float | None


class ResidentModel(BaseModel):
    name: str
    device: str
    estimated_bytes: int | None
    method: Literal["CUDA tensor storage", "estimate unavailable"]


class WorkerResources(BaseModel):
    identity: str
    lanes: list[str]
    sampled_at: datetime
    running: list[ResourceTask]
    reserved_count: int
    models: list[ResidentModel]
    cuda_reserved_bytes: dict[str, int]


class ResourceCapabilities(BaseModel):
    gpu: bool
    docker: bool
    workers: bool


class SystemResources(BaseModel):
    sampled_at: datetime
    host: HostResources
    nojoin: NojoinResources | None
    gpus: list[GpuResources]
    workers: list[WorkerResources]
    queues: dict[str, int] | None
    capabilities: ResourceCapabilities
