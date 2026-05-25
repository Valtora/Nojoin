from __future__ import annotations

from typing import Any
import asyncio
import os
import secrets
import shutil
import time

import httpx
import redis.asyncio as redis
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import Session, text

from backend.celery_app import celery_app
from backend.core.db import sync_engine
from backend.preload_models import check_model_status
from backend.utils.config_manager import async_get_system_api_keys, config_manager
from backend.utils.download_progress import get_download_progress, is_download_in_progress
from backend.utils.version import get_installed_version

HF_VALIDATE_URL = "https://huggingface.co/api/whoami-v2"
HF_VALIDATION_TTL_SECONDS = 300.0

_hf_validation_cache: tuple[float, str, dict[str, Any]] | None = None


def _build_component(
    status: str,
    label: str,
    detail: str,
    action: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    component: dict[str, Any] = {
        "status": status,
        "label": label,
        "detail": detail,
        "action": action,
    }
    component.update(extra)
    return component


def _get_redis_url() -> str:
    return os.getenv("REDIS_URL", "redis://localhost:6379/0")


async def _get_db_component() -> tuple[dict[str, Any], bool]:
    try:
        with Session(sync_engine) as session:
            session.execute(text("SELECT 1"))
        return (
            _build_component(
                "ok",
                "Connected",
                "PostgreSQL responded to a simple health query.",
            ),
            True,
        )
    except Exception:
        return (
            _build_component(
                "error",
                "Disconnected",
                "The API could not reach PostgreSQL.",
                "Check the database container, credentials, and network routing.",
            ),
            False,
        )


async def _get_queue_component() -> tuple[dict[str, Any], bool]:
    client = None
    try:
        client = redis.from_url(_get_redis_url())
        await client.ping()
        return (
            _build_component(
                "ok",
                "Reachable",
                "Redis is reachable for Celery task dispatch and worker heartbeats.",
            ),
            True,
        )
    except Exception:
        return (
            _build_component(
                "error",
                "Unreachable",
                "Redis is unavailable, so queued processing cannot be dispatched reliably.",
                "Check the Redis container and queue connection settings.",
            ),
            False,
        )
    finally:
        if client is not None:
            await client.close()


async def _resolve_worker_status() -> str:
    worker_status = "unknown"
    heartbeat_client = None

    try:
        heartbeat_client = redis.from_url(_get_redis_url())
        if await heartbeat_client.get("nojoin:worker:heartbeat"):
            worker_status = "active"
    except Exception:
        pass
    finally:
        if heartbeat_client is not None:
            await heartbeat_client.close()

    if worker_status != "active":
        try:
            inspector = celery_app.control.inspect()
            active_workers = inspector.ping()
            worker_status = "active" if active_workers else "inactive"
        except Exception:
            worker_status = "error"

    return worker_status


async def _get_worker_component() -> tuple[dict[str, Any], str]:
    worker_status = await _resolve_worker_status()

    if worker_status == "active":
        return (
            _build_component(
                "ok",
                "Active",
                "At least one Celery worker responded to a heartbeat or direct ping.",
            ),
            worker_status,
        )

    if worker_status == "inactive":
        return (
            _build_component(
                "error",
                "Inactive",
                "No Celery worker responded, so live and final processing jobs cannot run.",
                "Start the worker container and confirm it can connect to Redis.",
            ),
            worker_status,
        )

    return (
        _build_component(
            "error",
            "Unavailable",
            "The API could not confirm worker status.",
            "Check the worker container logs and broker connectivity.",
        ),
        worker_status,
    )


def _get_ffmpeg_component() -> tuple[dict[str, Any], bool]:
    try:
        from backend.utils.audio import ensure_ffmpeg_in_path

        ensure_ffmpeg_in_path()
    except Exception:
        pass

    ffmpeg_path = shutil.which("ffmpeg")
    ffprobe_path = shutil.which("ffprobe")
    ready = bool(ffmpeg_path and ffprobe_path)

    if ready:
        return (
            _build_component(
                "ok",
                "Ready",
                "ffmpeg and ffprobe are available for audio preprocessing.",
                None,
                ffmpeg_path=ffmpeg_path,
                ffprobe_path=ffprobe_path,
            ),
            True,
        )

    return (
        _build_component(
            "error",
            "Missing",
            "ffmpeg or ffprobe is unavailable, so recordings cannot be processed safely.",
            "Install ffmpeg and restart the API and worker services.",
            ffmpeg_path=ffmpeg_path,
            ffprobe_path=ffprobe_path,
        ),
        False,
    )


def _current_download_summary() -> dict[str, Any]:
    progress = get_download_progress()
    return {
        "in_progress": bool(progress and is_download_in_progress()),
        "status": progress.get("status") if progress else None,
        "stage": progress.get("stage") if progress else None,
        "message": progress.get("message") if progress else None,
        "progress": progress.get("progress") if progress else None,
    }


def _is_stage_downloading(download: dict[str, Any], *stages: str) -> bool:
    return bool(download.get("in_progress") and download.get("stage") in stages)


def _get_transcription_component(
    model_status: dict[str, Any],
    download: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    transcription_backend = str(config_manager.get("transcription_backend", "whisper"))
    whisper_model_size = str(config_manager.get("whisper_model_size", "turbo"))

    if transcription_backend == "parakeet":
        configured_model = str(
            config_manager.get("parakeet_model", "parakeet-tdt-0.6b-v3")
        )
        backing_status = model_status.get("parakeet", {})
        model_label = f"Parakeet ({configured_model})"
        downloading = _is_stage_downloading(download, "whisper", "init")
    elif transcription_backend == "canary":
        configured_model = str(
            config_manager.get("canary_model", "nemo-canary-1b-v2")
        )
        backing_status = model_status.get("canary", {})
        model_label = f"Canary ({configured_model})"
        downloading = _is_stage_downloading(download, "whisper", "init")
    else:
        configured_model = whisper_model_size
        backing_status = model_status.get("whisper", {})
        model_label = f"Whisper {configured_model}"
        downloading = _is_stage_downloading(download, "whisper", "init")

    downloaded = bool(backing_status.get("downloaded"))

    if downloaded:
        return (
            _build_component(
                "ok",
                "Ready",
                f"{model_label} is cached and ready for transcription.",
                None,
                backend=transcription_backend,
                configured_model=configured_model,
                downloaded=True,
                path=backing_status.get("path"),
            ),
            True,
        )

    if downloading:
        return (
            _build_component(
                "warning",
                "Downloading",
                f"{model_label} is being downloaded to the local model cache.",
                None,
                backend=transcription_backend,
                configured_model=configured_model,
                downloaded=False,
                path=backing_status.get("path"),
            ),
            False,
        )

    return (
        _build_component(
            "error",
            "Missing",
            f"{model_label} is not present in the local model cache.",
            "Download the configured transcription model before starting new recordings.",
            backend=transcription_backend,
            configured_model=configured_model,
            downloaded=False,
            path=backing_status.get("path"),
        ),
        False,
    )


async def _validate_hf_token(token: str | None) -> dict[str, Any]:
    global _hf_validation_cache

    if not token:
        _hf_validation_cache = None
        return {
            "configured": False,
            "valid": None,
            "status": "warning",
            "detail": "No Hugging Face token is configured for Pyannote.",
            "action": "Add a Hugging Face token and accept the Pyannote model terms.",
        }

    now = time.monotonic()
    cached = _hf_validation_cache
    if cached is not None:
        cached_at, cached_token, cached_result = cached
        if now - cached_at < HF_VALIDATION_TTL_SECONDS and secrets.compare_digest(
            cached_token,
            token,
        ):
            return cached_result

    _hf_validation_cache = None

    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            response = await client.get(
                HF_VALIDATE_URL,
                headers={"Authorization": f"Bearer {token}"},
            )

        if response.status_code == 200:
            result = {
                "configured": True,
                "valid": True,
                "status": "ok",
                "detail": "The Hugging Face token was validated successfully.",
                "action": None,
            }
        elif response.status_code in {401, 403}:
            result = {
                "configured": True,
                "valid": False,
                "status": "error",
                "detail": "The configured Hugging Face token was rejected.",
                "action": "Update the token and accept the required Pyannote model terms.",
            }
        else:
            result = {
                "configured": True,
                "valid": None,
                "status": "warning",
                "detail": f"Hugging Face validation returned HTTP {response.status_code}.",
                "action": "Retry the check or verify outbound network access from the API container.",
            }
    except httpx.HTTPError:
        result = {
            "configured": True,
            "valid": None,
            "status": "warning",
            "detail": "The API could not validate the Hugging Face token right now.",
            "action": "Check outbound network access or validate the token manually.",
        }

    _hf_validation_cache = (now, token, result)
    return result


async def _get_diarization_component(
    db: AsyncSession,
    model_status: dict[str, Any],
    download: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    diarization_enabled = bool(config_manager.get("enable_diarization", True))
    pyannote_ready = bool(model_status.get("pyannote", {}).get("downloaded"))
    embedding_ready = bool(model_status.get("embedding", {}).get("downloaded"))
    system_keys = await async_get_system_api_keys(db)
    hf_token_status = await _validate_hf_token(system_keys.get("hf_token"))
    downloading = _is_stage_downloading(download, "pyannote", "embedding", "init")

    if not diarization_enabled:
        return (
            _build_component(
                "disabled",
                "Disabled",
                "Speaker diarization is disabled, so transcripts will remain transcription-only.",
                "Enable diarization if you want speaker separation in transcripts.",
                enabled=False,
                token_configured=hf_token_status["configured"],
                token_valid=hf_token_status["valid"],
                pyannote_downloaded=pyannote_ready,
                embedding_downloaded=embedding_ready,
            ),
            False,
        )

    if pyannote_ready and embedding_ready and hf_token_status["valid"] is True:
        return (
            _build_component(
                "ok",
                "Ready",
                "Pyannote diarization, speaker embeddings, and Hugging Face access are ready.",
                None,
                enabled=True,
                token_configured=True,
                token_valid=True,
                pyannote_downloaded=True,
                embedding_downloaded=True,
            ),
            True,
        )

    if downloading and (not pyannote_ready or not embedding_ready):
        detail = "Pyannote assets are still downloading. Speaker diarization will remain unavailable until the download completes."
        action = None
    elif not hf_token_status["configured"]:
        detail = "Speaker diarization is enabled, but no Hugging Face token is configured. Core transcription can still run without speaker separation."
        action = hf_token_status["action"]
    elif hf_token_status["valid"] is False:
        detail = "Speaker diarization is enabled, but the configured Hugging Face token is not valid."
        action = hf_token_status["action"]
    elif not pyannote_ready or not embedding_ready:
        missing_assets = []
        if not pyannote_ready:
            missing_assets.append("Pyannote diarization model")
        if not embedding_ready:
            missing_assets.append("speaker embedding model")
        detail = f"{', '.join(missing_assets)} missing from the local cache."
        action = "Download the missing diarization assets before relying on speaker separation."
    else:
        detail = hf_token_status["detail"]
        action = hf_token_status["action"]

    return (
        _build_component(
            "warning",
            "Fallback active",
            detail,
            action,
            enabled=True,
            token_configured=hf_token_status["configured"],
            token_valid=hf_token_status["valid"],
            pyannote_downloaded=pyannote_ready,
            embedding_downloaded=embedding_ready,
        ),
        False,
    )


async def _get_device_component(worker_status: str) -> tuple[dict[str, Any], bool]:
    requested_device = str(config_manager.get("processing_device", "auto"))
    if requested_device == "auto" and not bool(config_manager.get("use_gpu", True)):
        requested_device = "cpu"

    if worker_status != "active":
        return (
            _build_component(
                "warning",
                "Worker unavailable",
                "The worker must be active before Nojoin can confirm the current processing device.",
                "Bring the worker online to verify GPU or CPU execution mode.",
                requested_device=requested_device,
                active_device=None,
                gpu_name=None,
                torch_version=None,
            ),
            False,
        )

    try:
        from backend.worker.tasks import get_worker_device_status

        async_result = get_worker_device_status.delay()  # type: ignore[attr-defined]
        payload = await asyncio.to_thread(async_result.get, timeout=5)
    except Exception:
        return (
            _build_component(
                "warning",
                "Unknown",
                "The API could not confirm the worker's processing device.",
                "Check the worker logs if GPU readiness looks incorrect.",
                requested_device=requested_device,
                active_device=None,
                gpu_name=None,
                torch_version=None,
            ),
            False,
        )

    active_device = str(payload.get("device", "unknown"))
    gpu_name = payload.get("gpu_name")
    torch_version = payload.get("torch_version")

    if active_device == "cuda":
        return (
            _build_component(
                "ok",
                "GPU ready",
                f"The worker reports CUDA availability{f' on {gpu_name}' if gpu_name else ''}.",
                None,
                requested_device=requested_device,
                active_device=active_device,
                gpu_name=gpu_name,
                torch_version=torch_version,
            ),
            True,
        )

    if active_device == "cpu":
        if requested_device == "cpu":
            return (
                _build_component(
                    "ok",
                    "CPU ready",
                    "The worker is configured to process recordings on CPU.",
                    None,
                    requested_device=requested_device,
                    active_device=active_device,
                    gpu_name=None,
                    torch_version=torch_version,
                ),
                True,
            )

        return (
            _build_component(
                "warning",
                "CPU fallback",
                "The worker is processing on CPU, so live and final stages will run more slowly than the normal GPU path.",
                "Check NVIDIA runtime access if you expect GPU acceleration.",
                requested_device=requested_device,
                active_device=active_device,
                gpu_name=None,
                torch_version=torch_version,
            ),
            False,
        )

    return (
        _build_component(
            "warning",
            "Unknown",
            payload.get("error", "The worker reported an unknown processing device."),
            "Inspect the worker logs to verify device detection.",
            requested_device=requested_device,
            active_device=active_device,
            gpu_name=gpu_name,
            torch_version=torch_version,
        ),
        False,
    )


async def _get_optional_ai_component(db: AsyncSession) -> dict[str, Any]:
    system_keys = await async_get_system_api_keys(db)
    has_cloud_provider = any(
        bool(system_keys.get(key))
        for key in ("gemini_api_key", "openai_api_key", "anthropic_api_key")
    )
    has_ollama = bool(config_manager.get("ollama_api_url")) and bool(
        config_manager.get("ollama_model")
    )
    configured = has_cloud_provider or has_ollama

    if configured:
        return _build_component(
            "ok",
            "Configured",
            "Optional AI enhancement is configured separately from the core transcription pipeline.",
            None,
            configured=True,
        )

    return _build_component(
        "info",
        "Not configured",
        "Optional AI enhancement is not configured. Core transcription and diarization checks still apply independently.",
        "Add provider credentials in AI settings if you want titles, notes, or Meeting Edge generation.",
        configured=False,
    )


def _build_summary(
    checks: dict[str, dict[str, Any]],
    *,
    transcription_ready: bool,
    diarization_ready: bool,
    device_ready: bool,
) -> dict[str, Any]:
    blocking_reasons: list[str] = []
    degraded_reasons: list[str] = []

    if checks["database"]["status"] != "ok":
        blocking_reasons.append("Database connectivity is unavailable.")
    if checks["queue"]["status"] != "ok":
        blocking_reasons.append("Redis queue connectivity is unavailable.")
    if checks["worker"]["status"] != "ok":
        blocking_reasons.append("No active worker is available for processing tasks.")
    if checks["ffmpeg"]["status"] != "ok":
        blocking_reasons.append("ffmpeg or ffprobe is missing.")
    if not transcription_ready:
        blocking_reasons.append("The configured transcription model is not ready.")

    diarization_enabled = checks["diarization"].get("enabled") is not False
    if diarization_enabled and not diarization_ready:
        degraded_reasons.append("Speaker diarization will fall back until its prerequisites are ready.")
    if checks["device"]["status"] == "warning" and not device_ready:
        degraded_reasons.append("Processing is running without the normal GPU acceleration path.")

    if blocking_reasons:
        return {
            "pipeline_status": "blocked",
            "message": "Core processing is blocked until the highlighted readiness issues are fixed.",
            "blocking_reasons": blocking_reasons,
            "degraded_reasons": degraded_reasons,
        }

    if degraded_reasons:
        return {
            "pipeline_status": "degraded",
            "message": "Core transcription is ready, but some processing capabilities are in fallback mode.",
            "blocking_reasons": [],
            "degraded_reasons": degraded_reasons,
        }

    return {
        "pipeline_status": "ready",
        "message": "Live and final processing prerequisites are ready.",
        "blocking_reasons": [],
        "degraded_reasons": [],
    }

async def get_system_health_status() -> dict[str, Any]:
    health_status = {
        "status": "ok",
        "version": get_installed_version(),
        "components": {
            "db": "unknown",
            "worker": "unknown",
        },
    }

    db_component, db_ready = await _get_db_component()
    health_status["components"]["db"] = "connected" if db_ready else "disconnected"
    if not db_ready:
        health_status["status"] = "error"

    worker_status = await _resolve_worker_status()
    health_status["components"]["worker"] = worker_status

    if worker_status in ["inactive", "error"] and health_status["status"] == "ok":
        health_status["status"] = "warning"

    return health_status


async def get_admin_health_status(db: AsyncSession) -> dict[str, Any]:
    model_status = check_model_status(
        whisper_model_size=str(config_manager.get("whisper_model_size", "turbo"))
    )
    download = _current_download_summary()

    database_component, _ = await _get_db_component()
    queue_component, _ = await _get_queue_component()
    worker_component, worker_status = await _get_worker_component()
    ffmpeg_component, _ = _get_ffmpeg_component()
    transcription_component, transcription_ready = _get_transcription_component(
        model_status,
        download,
    )
    diarization_component, diarization_ready = await _get_diarization_component(
        db,
        model_status,
        download,
    )
    device_component, device_ready = await _get_device_component(worker_status)
    optional_ai_component = await _get_optional_ai_component(db)

    checks = {
        "database": database_component,
        "queue": queue_component,
        "worker": worker_component,
        "ffmpeg": ffmpeg_component,
        "transcription_model": transcription_component,
        "diarization": diarization_component,
        "device": device_component,
        "optional_ai": optional_ai_component,
    }
    summary = _build_summary(
        checks,
        transcription_ready=transcription_ready,
        diarization_ready=diarization_ready,
        device_ready=device_ready,
    )

    status = "ok"
    if summary["pipeline_status"] == "degraded":
        status = "warning"
    elif summary["pipeline_status"] == "blocked":
        status = "error"

    return {
        "status": status,
        "version": get_installed_version(),
        "summary": summary,
        "checks": checks,
        "download": download,
    }