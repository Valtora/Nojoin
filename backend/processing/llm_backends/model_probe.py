"""Test connection's model probe: one small real generation per model.

Listing a provider's models proves the key and nothing about the model a
setting names (#990: the list answered while every real call 404'd). A probe
sends a tiny prompt through the backend's own client, with the options and
JSON mode its Meeting Edge call uses, and checks the reply parses.

The prompt, the reply check, the failure classification and the wording live
here; each backend only sends the request (``LLMBackend.probe_model``).
"""

from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from typing import Callable, Iterable, Literal, Sequence

logger = logging.getLogger(__name__)

ProbeKind = Literal[
    "ok", "unknown_model", "auth", "quota", "timeout", "bad_response", "error"
]
ProbeRole = Literal["main", "meeting_edge"]

PROBE_PROMPT = 'Reply with exactly this JSON object and nothing else: {"ok": true}'
# A ceiling, not a cost: the reply is about five tokens. Only Anthropic gets
# it, since its API requires one; it leaves a model that thinks first room to
# answer. The other providers' real calls send no cap, so their probes do not.
PROBE_MAX_OUTPUT_TOKENS = 2048
PROBE_TIMEOUT_SECONDS = 20
# A cold Ollama model has to load before it answers.
OLLAMA_PROBE_TIMEOUT_SECONDS = 60
MAX_PROBES_PER_REQUEST = 3
_DETAIL_MAX_CHARS = 160

PROVIDER_LABELS = {
    "gemini": "Gemini",
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "ollama": "Ollama",
}


class ProbeReplyError(ValueError):
    """The model answered, but not with the JSON object the probe asked for."""


@dataclass(frozen=True)
class ProbeResult:
    model: str
    role: ProbeRole
    ok: bool
    kind: ProbeKind
    message: str

    def as_dict(self) -> dict:
        return asdict(self)


def probe_timeout(provider: str) -> int:
    return (
        OLLAMA_PROBE_TIMEOUT_SECONDS if provider == "ollama" else PROBE_TIMEOUT_SECONDS
    )


def check_probe_reply(text: str | None) -> None:
    """Raise ProbeReplyError unless the reply is the object the prompt asked for.

    A fenced reply is accepted, as the real calls' parsers accept one.
    """
    stripped = (text or "").strip()
    if not stripped:
        raise ProbeReplyError("empty reply")
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", stripped, re.DOTALL)
    if fenced:
        stripped = fenced.group(1)
    try:
        payload = json.loads(stripped)
    except ValueError as exc:
        raise ProbeReplyError("reply is not JSON") from exc
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise ProbeReplyError("reply is not the requested object")


def _exception_chain(exc: BaseException) -> Iterable[BaseException]:
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def _status_code(exc: BaseException) -> int | None:
    for candidate in (
        getattr(exc, "status_code", None),
        getattr(exc, "code", None),
        getattr(getattr(exc, "response", None), "status_code", None),
    ):
        if isinstance(candidate, int) and not isinstance(candidate, bool):
            return candidate
    return None


def is_request_refusal(exc: BaseException) -> bool:
    """Whether the provider refused the request itself (400 or 422)."""
    return any(_status_code(link) in (400, 422) for link in _exception_chain(exc))


def classify_probe_error(exc: BaseException) -> ProbeKind:
    """Sort a failed probe by what the user can do about it.

    SDK errors share no base class, so this reads the HTTP status each SDK
    carries (``status_code``, ``code`` or ``response.status_code``) and the
    class names timeouts use across requests, httpx, httpx2 and the SDKs.
    """
    for link in _exception_chain(exc):
        if isinstance(link, ProbeReplyError):
            return "bad_response"
        # urllib3's NewConnectionError subclasses ConnectTimeoutError, so a
        # refused connection would otherwise read as a timeout.
        if isinstance(link, ConnectionRefusedError) or any(
            cls.__name__ == "NewConnectionError" for cls in type(link).__mro__
        ):
            return "error"
        if isinstance(link, TimeoutError) or any(
            "Timeout" in cls.__name__ for cls in type(link).__mro__
        ):
            return "timeout"
        status = _status_code(link)
        if status == 404:
            return "unknown_model"
        if status in (401, 403):
            return "auth"
        if status == 429:
            return "quota"
    return "error"


_URL = re.compile(r"[a-z][a-z0-9+.-]*://\S+", re.IGNORECASE)
_KEY_LIKE = re.compile(
    r"(?:sk-[A-Za-z0-9_-]{8,}|AIza[0-9A-Za-z_-]{20,}|"
    r"(?:key|token|secret|password)\s*[=:]\s*\S+)",
    re.IGNORECASE,
)


def safe_provider_detail(exc: BaseException, secrets: Sequence[str] = ()) -> str:
    """A short, single-line provider reason with no key and no URL in it.

    URLs are dropped whole, since one can carry credentials or a key in its
    query, and so is anything shaped like a key, besides the exact secrets the
    caller passes.
    """
    text = " ".join(str(exc).split())
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[redacted]")
    text = _URL.sub("[url]", text)
    text = _KEY_LIKE.sub("[redacted]", text)
    if len(text) > _DETAIL_MAX_CHARS:
        text = text[: _DETAIL_MAX_CHARS - 3].rstrip() + "..."
    return text


def probe_message(provider: str, model: str, kind: ProbeKind, timeout: int) -> str:
    label = PROVIDER_LABELS.get(provider, provider)
    if kind == "ok":
        return f"{model} answered."
    if kind == "unknown_model":
        if provider == "ollama":
            return f"{model} is not on the Ollama server. Pull it, or choose another model."
        return f"{label} does not serve {model}. Choose another model."
    if kind == "auth":
        return f"{label} refused the credential for {model}."
    if kind == "quota":
        return f"{label} is rate limiting {model}, or its quota is used up."
    if kind == "timeout":
        if provider == "ollama":
            return (
                f"{model} did not answer within {timeout} seconds. It may still "
                "be loading; try again in a minute."
            )
        return f"{model} did not answer within {timeout} seconds."
    if kind == "bad_response":
        return f"{model} answered, but not with the JSON Nojoin asked for."
    return f"{model} failed with a provider error."


def probe_one(
    build_backend: Callable[[str], object],
    provider: str,
    model: str,
    role: ProbeRole,
    *,
    include_detail: bool,
    timeout: int | None = None,
    secrets: Sequence[str] = (),
) -> ProbeResult:
    """Run one probe and describe the outcome. Never raises."""
    seconds = timeout if timeout is not None else probe_timeout(provider)
    try:
        backend = build_backend(model)
        reply = backend.probe_model(  # type: ignore[attr-defined]
            PROBE_PROMPT,
            timeout=seconds,
            max_output_tokens=PROBE_MAX_OUTPUT_TOKENS,
        )
        check_probe_reply(reply)
    except Exception as exc:  # noqa: BLE001 -- boundary: a probe reports every failure as a result row, never as an error
        kind = classify_probe_error(exc)
        logger.warning(
            "Model probe failed: provider=%s model=%s role=%s kind=%s error=%s",
            provider,
            model,
            role,
            kind,
            safe_provider_detail(exc, secrets),
        )
        message = probe_message(provider, model, kind, seconds)
        if include_detail and kind != "bad_response":
            detail = safe_provider_detail(exc, secrets)
            if detail:
                message = f"{message} ({detail})"
        return ProbeResult(model=model, role=role, ok=False, kind=kind, message=message)
    return ProbeResult(
        model=model,
        role=role,
        ok=True,
        kind="ok",
        message=probe_message(provider, model, "ok", seconds),
    )


def plan_probes(
    requested: Iterable[tuple[str, ProbeRole]],
) -> list[tuple[str, ProbeRole]]:
    """Each distinct model once, in request order, at most three."""
    planned: list[tuple[str, ProbeRole]] = []
    seen: set[str] = set()
    for model, role in requested:
        name = (model or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        planned.append((name, role))
    return planned[:MAX_PROBES_PER_REQUEST]


def run_probes(
    build_backend: Callable[[str], object],
    provider: str,
    probes: Sequence[tuple[str, ProbeRole]],
    *,
    include_detail: bool,
    timeout: int | None = None,
    secrets: Sequence[str] = (),
) -> list[ProbeResult]:
    """Probe each planned model; hosted providers in parallel, Ollama in turn.

    One Ollama server loading several cold models at once can push each past
    its timeout, so its probes run one after another.
    """

    def run(item: tuple[str, ProbeRole]) -> ProbeResult:
        model, role = item
        return probe_one(
            build_backend,
            provider,
            model,
            role,
            include_detail=include_detail,
            timeout=timeout,
            secrets=secrets,
        )

    if provider == "ollama" or len(probes) < 2:
        return [run(item) for item in probes]
    with ThreadPoolExecutor(max_workers=len(probes)) as pool:
        return list(pool.map(run, probes))


def summarise(provider: str, results: Sequence[ProbeResult]) -> str:
    label = PROVIDER_LABELS.get(provider, provider)
    failed = [result for result in results if not result.ok]
    if not results:
        return f"{label} is reachable. Choose a model to test it."
    if not failed:
        names = ", ".join(result.model for result in results)
        return f"{label} works: {names} answered."
    if len(results) == 1:
        return f"{label} is reachable, but the model failed the test."
    return f"{len(failed)} of {len(results)} {label} models failed the test."
