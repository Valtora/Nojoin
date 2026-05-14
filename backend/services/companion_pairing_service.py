from __future__ import annotations

from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import Iterable
from urllib.parse import urlencode, urlparse
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from backend.core import companion_identity
from backend.core import security
from backend.core.encryption import decrypt_secret, encrypt_secret
from backend.models.companion_pairing import (
    CompanionPairing,
    CompanionPairingRevocationReason,
    CompanionPairingStatus,
)
from backend.models.companion_pairing_request import (
    CompanionPairingRequest,
    CompanionPairingRequestStatus,
)
from backend.models.user import User
from backend.utils.config_manager import get_trusted_web_origin
from backend.utils.time import utc_now


PAIRING_REQUEST_EXPIRE_SECONDS = 300


TERMINAL_PAIRING_REQUEST_STATUSES = frozenset(
    {
        CompanionPairingRequestStatus.COMPLETED.value,
        CompanionPairingRequestStatus.DECLINED.value,
        CompanionPairingRequestStatus.CANCELLED.value,
        CompanionPairingRequestStatus.EXPIRED.value,
        CompanionPairingRequestStatus.FAILED.value,
    }
)


class CompanionPairingStateError(Exception):
    def __init__(self, detail: str, *, status_code: int = 409):
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


@dataclass(frozen=True)
class PreparedCompanionPairingPayload:
    pairing_code: str
    companion_credential_secret: str
    api_protocol: str
    api_host: str
    api_port: int
    tls_fingerprint: str | None
    local_control_secret: str
    local_control_secret_version: int
    backend_pairing_id: str


@dataclass(frozen=True)
class ActiveCompanionPairingAuth:
    pairing_session_id: str
    paired_web_origin: str
    local_control_secret: str
    local_control_secret_version: int


@dataclass(frozen=True)
class CreatedCompanionPairingRequest:
    request_id: str
    launch_url: str
    status: str
    expires_at: datetime
    backend_origin: str
    replacement: bool


@dataclass(frozen=True)
class CompanionPairingRequestStatusView:
    request_id: str
    status: str
    expires_at: datetime
    opened_at: datetime | None
    completed_at: datetime | None
    detail: str | None
    backend_origin: str
    replacement: bool


@dataclass(frozen=True)
class CompletedCompanionPairingRequest:
    api_protocol: str
    api_host: str
    api_port: int
    paired_web_origin: str
    companion_credential_secret: str
    local_control_secret: str
    local_control_secret_version: int
    backend_pairing_id: str
    backend_identity_key_id: str
    backend_identity_public_key: str


@dataclass(frozen=True)
class CompanionExchangeUser:
    id: int
    username: str
    is_active: bool


@dataclass(frozen=True)
class CompanionCredentialExchangeResult:
    user: CompanionExchangeUser
    pairing_session_id: str
    activated: bool


def _normalize_origin(origin: str | None) -> str | None:
    if not origin:
        return None

    parsed = urlparse(origin.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None

    default_port = 443 if parsed.scheme == "https" else 80
    if parsed.port and parsed.port != default_port:
        return f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"
    return f"{parsed.scheme}://{parsed.hostname}"


def normalize_origin(origin: str | None) -> str | None:
    return _normalize_origin(origin)


def _parse_api_origin(origin: str) -> tuple[str, str, int]:
    parsed = urlparse(origin)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise CompanionPairingStateError(
            "Backend pairing origin is not configured correctly.",
            status_code=500,
        )

    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return parsed.scheme, parsed.hostname, port


def _build_origin(protocol: str, host: str, port: int) -> str:
    default_port = 443 if protocol == "https" else 80
    if port == default_port:
        return f"{protocol}://{host}"
    return f"{protocol}://{host}:{port}"


def _build_pairing_launch_url(
    *,
    request_id: str,
    request_secret: str,
    backend_origin: str,
    username: str,
    replacement: bool,
    expires_at: datetime,
) -> str:
    identity = companion_identity.get_backend_identity()
    fields = {
        "backend_origin": backend_origin,
        "expires_at": str(int(expires_at.timestamp())),
        "key_id": identity.key_id,
        "replacement": "1" if replacement else "0",
        "request_id": request_id,
        "request_secret": request_secret,
        "username": username,
        "version": "1",
    }
    signature = companion_identity.sign_backend_identity_fields(fields)
    query = urlencode(
        {
            **fields,
            "public_key": identity.public_key,
            "signature": signature,
        }
    )
    return f"nojoin://pair?{query}"


def _request_is_terminal(row: CompanionPairingRequest) -> bool:
    return row.status in TERMINAL_PAIRING_REQUEST_STATUSES


def _set_request_terminal(
    row: CompanionPairingRequest,
    *,
    status: CompanionPairingRequestStatus,
    detail: str | None,
    failure_reason: str | None = None,
    completed_pairing_session_id: str | None = None,
) -> None:
    row.status = status.value
    row.status_detail = detail
    row.failure_reason = failure_reason
    row.completed_pairing_session_id = completed_pairing_session_id
    row.completed_at = utc_now()


def _expire_request_if_needed(row: CompanionPairingRequest) -> bool:
    if _request_is_terminal(row) or row.expires_at > utc_now():
        return False

    _set_request_terminal(
        row,
        status=CompanionPairingRequestStatus.EXPIRED,
        detail="Pairing request expired before it was approved in the Companion app.",
        failure_reason="expired",
    )
    return True


def _verify_request_secret(secret: str, expected_hash: str) -> bool:
    return security.verify_companion_credential_secret(secret, expected_hash)


def _serialize_request_status(
    row: CompanionPairingRequest,
) -> CompanionPairingRequestStatusView:
    return CompanionPairingRequestStatusView(
        request_id=row.request_id,
        status=row.status,
        expires_at=row.expires_at,
        opened_at=row.opened_at,
        completed_at=row.completed_at,
        detail=row.status_detail,
        backend_origin=_build_origin(row.api_protocol, row.api_host, row.api_port),
        replacement=bool(row.replacement_pairing_session_id),
    )


async def _persist_request(db: AsyncSession, row: CompanionPairingRequest) -> None:
    db.add(row)
    await db.commit()
    await db.refresh(row)


async def _mark_failed_request(
    db: AsyncSession,
    row: CompanionPairingRequest,
    *,
    detail: str,
    failure_reason: str,
    status_code: int = 409,
) -> None:
    _set_request_terminal(
        row,
        status=CompanionPairingRequestStatus.FAILED,
        detail=detail,
        failure_reason=failure_reason,
    )
    await _persist_request(db, row)
    raise CompanionPairingStateError(detail, status_code=status_code)


def _ensure_complete_pairing(row: CompanionPairing) -> None:
    if (
        not row.paired_web_origin
        or not row.api_protocol
        or not row.api_host
        or row.api_port <= 0
        or not row.companion_credential_hash
        or not row.local_control_secret_encrypted
        or row.local_control_secret_version < 1
    ):
        raise CompanionPairingStateError(
            "Companion pairing state is incomplete. Revoke the pairing and pair again."
        )


def _ensure_revoked_pairing_is_clean(row: CompanionPairing) -> None:
    if row.companion_credential_hash is not None:
        raise CompanionPairingStateError(
            "Companion pairing cleanup is incomplete. Revoke the pairing and pair again."
        )
    if row.local_control_secret_encrypted is not None:
        raise CompanionPairingStateError(
            "Companion pairing cleanup is incomplete. Revoke the pairing and pair again."
        )
    if row.revoked_at is None or not row.revocation_reason:
        raise CompanionPairingStateError(
            "Companion pairing revocation metadata is incomplete. Revoke the pairing and pair again."
        )


def _group_pairings(
    rows: Iterable[CompanionPairing],
) -> tuple[list[CompanionPairing], list[CompanionPairing], list[CompanionPairing]]:
    active_rows: list[CompanionPairing] = []
    pending_rows: list[CompanionPairing] = []
    revoked_rows: list[CompanionPairing] = []

    for row in rows:
        if row.status == CompanionPairingStatus.ACTIVE.value:
            _ensure_complete_pairing(row)
            if row.revoked_at is not None or row.revocation_reason:
                raise CompanionPairingStateError(
                    "Companion pairing state is conflicting. Revoke the pairing and pair again."
                )
            active_rows.append(row)
        elif row.status == CompanionPairingStatus.PENDING.value:
            _ensure_complete_pairing(row)
            if row.revoked_at is not None or row.revocation_reason:
                raise CompanionPairingStateError(
                    "Companion pairing state is conflicting. Revoke the pairing and pair again."
                )
            pending_rows.append(row)
        elif row.status == CompanionPairingStatus.REVOKED.value:
            _ensure_revoked_pairing_is_clean(row)
            revoked_rows.append(row)
        else:
            raise CompanionPairingStateError(
                "Companion pairing state is invalid. Revoke the pairing and pair again."
            )

    if len(active_rows) > 1 or len(pending_rows) > 1:
        raise CompanionPairingStateError(
            "Companion pairing state is conflicting. Revoke the pairing and pair again."
        )

    return active_rows, pending_rows, revoked_rows


async def _load_pairings_for_user(db: AsyncSession, user_id: int) -> list[CompanionPairing]:
    result = await db.execute(
        select(CompanionPairing)
        .where(CompanionPairing.user_id == user_id)
        .order_by(
            CompanionPairing.local_control_secret_version.desc(),
            CompanionPairing.created_at.desc(),
        )
    )
    return list(result.scalars().all())


async def _load_pairing_requests_for_user(
    db: AsyncSession,
    user_id: int,
) -> list[CompanionPairingRequest]:
    result = await db.execute(
        select(CompanionPairingRequest)
        .where(CompanionPairingRequest.user_id == user_id)
        .order_by(CompanionPairingRequest.created_at.desc())
    )
    return list(result.scalars().all())


async def _load_pairing_request_by_id(
    db: AsyncSession,
    request_id: str,
) -> CompanionPairingRequest | None:
    result = await db.execute(
        select(CompanionPairingRequest).where(
            CompanionPairingRequest.request_id == request_id
        )
    )
    return result.scalar_one_or_none()


def _next_secret_version(rows: Iterable[CompanionPairing]) -> int:
    current = max((row.local_control_secret_version for row in rows), default=0)
    return current + 1


def _latest_live_secret_version(rows: Iterable[CompanionPairing]) -> int:
    current = max(
        (
            row.local_control_secret_version
            for row in rows
            if row.status != CompanionPairingStatus.REVOKED.value
        ),
        default=0,
    )
    return current


def _revoke_pairing_row(
    row: CompanionPairing,
    *,
    reason: CompanionPairingRevocationReason,
) -> None:
    row.status = CompanionPairingStatus.REVOKED.value
    row.companion_credential_hash = None
    row.local_control_secret_encrypted = None
    row.revoked_at = utc_now()
    row.revocation_reason = reason.value
    row.supersedes_pairing_session_id = None


async def create_companion_pairing_request(
    db: AsyncSession,
    *,
    current_user: User,
    paired_web_origin: str,
) -> CreatedCompanionPairingRequest:
    normalized_origin = _normalize_origin(paired_web_origin)
    if not normalized_origin:
        raise CompanionPairingStateError(
            "Pairing requests must include a valid Origin header.",
            status_code=400,
        )

    rows = await _load_pairings_for_user(db, current_user.id)
    active_rows, pending_rows, _ = _group_pairings(rows)
    active_row = active_rows[0] if active_rows else None
    latest_live_version = _latest_live_secret_version(rows)
    if active_row and active_row.local_control_secret_version != latest_live_version:
        raise CompanionPairingStateError(
            "Companion pairing state is stale. Revoke the pairing and pair again."
        )

    for row in pending_rows:
        _revoke_pairing_row(
            row,
            reason=CompanionPairingRevocationReason.PENDING_CANCELLED,
        )
        db.add(row)

    existing_requests = await _load_pairing_requests_for_user(db, current_user.id)
    for row in existing_requests:
        if _expire_request_if_needed(row):
            db.add(row)
            continue
        if _request_is_terminal(row):
            continue
        _set_request_terminal(
            row,
            status=CompanionPairingRequestStatus.CANCELLED,
            detail="Cancelled because a newer pairing request was created.",
            failure_reason="superseded",
        )
        db.add(row)

    api_protocol, api_host, api_port = _parse_api_origin(get_trusted_web_origin())
    backend_origin = _build_origin(api_protocol, api_host, api_port)
    request_id = uuid4().hex
    request_secret = security.generate_companion_credential_secret()
    expires_at = utc_now() + timedelta(seconds=PAIRING_REQUEST_EXPIRE_SECONDS)
    request_row = CompanionPairingRequest(
        user_id=current_user.id,
        request_id=request_id,
        request_secret_hash=security.hash_companion_credential_secret(request_secret),
        status=CompanionPairingRequestStatus.PENDING.value,
        api_protocol=api_protocol,
        api_host=api_host,
        api_port=api_port,
        paired_web_origin=normalized_origin,
        replacement_pairing_session_id=(
            active_row.pairing_session_id if active_row is not None else None
        ),
        expires_at=expires_at,
    )
    db.add(request_row)
    await db.commit()

    return CreatedCompanionPairingRequest(
        request_id=request_id,
        launch_url=_build_pairing_launch_url(
            request_id=request_id,
            request_secret=request_secret,
            backend_origin=backend_origin,
            username=current_user.username,
            replacement=active_row is not None,
            expires_at=expires_at,
        ),
        status=request_row.status,
        expires_at=expires_at,
        backend_origin=backend_origin,
        replacement=active_row is not None,
    )


async def get_companion_pairing_request_status(
    db: AsyncSession,
    *,
    current_user: User,
    request_id: str,
) -> CompanionPairingRequestStatusView:
    row = await _load_pairing_request_by_id(db, request_id.strip())
    if row is None or row.user_id != current_user.id:
        raise CompanionPairingStateError(
            "Companion pairing request was not found.",
            status_code=404,
        )

    if _expire_request_if_needed(row):
        await _persist_request(db, row)

    return _serialize_request_status(row)


async def cancel_companion_pairing_request(
    db: AsyncSession,
    *,
    current_user: User,
    request_id: str,
) -> int:
    row = await _load_pairing_request_by_id(db, request_id.strip())
    if row is None or row.user_id != current_user.id:
        raise CompanionPairingStateError(
            "Companion pairing request was not found.",
            status_code=404,
        )

    if _expire_request_if_needed(row):
        await _persist_request(db, row)
        return 0

    if _request_is_terminal(row):
        return 0

    _set_request_terminal(
        row,
        status=CompanionPairingRequestStatus.CANCELLED,
        detail="Pairing request cancelled before approval.",
        failure_reason="cancelled",
    )
    await _persist_request(db, row)
    return 1


async def mark_companion_pairing_request_opened(
    db: AsyncSession,
    *,
    request_id: str,
    request_secret: str,
) -> CompanionPairingRequestStatusView:
    row = await _load_pairing_request_by_id(request_id=request_id.strip(), db=db)
    if row is None:
        raise CompanionPairingStateError(
            "Companion pairing request is unknown. Start again from Nojoin.",
            status_code=404,
        )

    if not _verify_request_secret(request_secret.strip(), row.request_secret_hash):
        raise CompanionPairingStateError(
            "Companion pairing request secret is invalid.",
            status_code=401,
        )

    if _expire_request_if_needed(row):
        await _persist_request(db, row)
        raise CompanionPairingStateError(
            "Companion pairing request expired. Start again from Nojoin.",
            status_code=410,
        )

    if row.status == CompanionPairingRequestStatus.COMPLETED.value:
        return _serialize_request_status(row)
    if row.status in TERMINAL_PAIRING_REQUEST_STATUSES:
        raise CompanionPairingStateError(
            row.status_detail or "Companion pairing request is no longer active.",
            status_code=409,
        )

    changed = False
    if row.opened_at is None:
        row.opened_at = utc_now()
        changed = True
    if row.status == CompanionPairingRequestStatus.PENDING.value:
        row.status = CompanionPairingRequestStatus.OPENED.value
        row.status_detail = None
        changed = True

    if changed:
        await _persist_request(db, row)

    return _serialize_request_status(row)


async def reject_companion_pairing_request(
    db: AsyncSession,
    *,
    request_id: str,
    request_secret: str,
    status: CompanionPairingRequestStatus,
    detail: str,
    failure_reason: str,
) -> CompanionPairingRequestStatusView:
    row = await _load_pairing_request_by_id(request_id=request_id.strip(), db=db)
    if row is None:
        raise CompanionPairingStateError(
            "Companion pairing request is unknown. Start again from Nojoin.",
            status_code=404,
        )

    if not _verify_request_secret(request_secret.strip(), row.request_secret_hash):
        raise CompanionPairingStateError(
            "Companion pairing request secret is invalid.",
            status_code=401,
        )

    if _expire_request_if_needed(row):
        await _persist_request(db, row)
        raise CompanionPairingStateError(
            "Companion pairing request expired. Start again from Nojoin.",
            status_code=410,
        )

    if row.status == CompanionPairingRequestStatus.COMPLETED.value:
        raise CompanionPairingStateError(
            "Companion pairing request already completed.",
            status_code=409,
        )

    if _request_is_terminal(row):
        return _serialize_request_status(row)

    if row.opened_at is None:
        row.opened_at = utc_now()
    _set_request_terminal(
        row,
        status=status,
        detail=detail,
        failure_reason=failure_reason,
    )
    await _persist_request(db, row)
    return _serialize_request_status(row)


async def complete_companion_pairing_request(
    db: AsyncSession,
    *,
    request_id: str,
    request_secret: str,
    tls_fingerprint: str,
) -> CompletedCompanionPairingRequest:
    row = await _load_pairing_request_by_id(request_id=request_id.strip(), db=db)
    if row is None:
        raise CompanionPairingStateError(
            "Companion pairing request is unknown. Start again from Nojoin.",
            status_code=404,
        )

    if not _verify_request_secret(request_secret.strip(), row.request_secret_hash):
        raise CompanionPairingStateError(
            "Companion pairing request secret is invalid.",
            status_code=401,
        )

    normalized_fingerprint = tls_fingerprint.strip()
    if not normalized_fingerprint:
        raise CompanionPairingStateError(
            "Companion pairing completion requires a backend TLS fingerprint.",
            status_code=400,
        )

    if _expire_request_if_needed(row):
        await _persist_request(db, row)
        raise CompanionPairingStateError(
            "Companion pairing request expired. Start again from Nojoin.",
            status_code=410,
        )

    if row.status == CompanionPairingRequestStatus.COMPLETED.value:
        raise CompanionPairingStateError(
            "Companion pairing request already completed.",
            status_code=409,
        )

    if row.status in TERMINAL_PAIRING_REQUEST_STATUSES:
        raise CompanionPairingStateError(
            row.status_detail or "Companion pairing request is no longer active.",
            status_code=409,
        )

    if row.opened_at is None:
        row.opened_at = utc_now()
    row.status = CompanionPairingRequestStatus.COMPLETING.value
    row.status_detail = "Companion is completing the secure pairing handshake."
    await _persist_request(db, row)

    user_row = (
        await db.execute(
            select(User).where(User.id == row.user_id)
        )
    ).scalar_one_or_none()
    if user_row is None:
        await _mark_failed_request(
            db,
            row,
            detail="Companion pairing user no longer exists. Pair again from Nojoin.",
            failure_reason="missing_user",
            status_code=401,
        )
    if not user_row.is_active:
        await _mark_failed_request(
            db,
            row,
            detail="Inactive user",
            failure_reason="inactive_user",
            status_code=400,
        )

    rows = await _load_pairings_for_user(db, row.user_id)
    active_rows, pending_rows, _ = _group_pairings(rows)
    active_row = active_rows[0] if active_rows else None
    latest_live_version = _latest_live_secret_version(rows)
    if active_row and active_row.local_control_secret_version != latest_live_version:
        await _mark_failed_request(
            db,
            row,
            detail="Companion pairing state is stale. Revoke the pairing and pair again.",
            failure_reason="stale_pairing_state",
        )

    if (
        row.replacement_pairing_session_id
        and active_row is not None
        and active_row.pairing_session_id != row.replacement_pairing_session_id
    ):
        await _mark_failed_request(
            db,
            row,
            detail="Companion pairing request is stale because a newer backend pairing is already active.",
            failure_reason="stale_request",
        )

    for pending_row in pending_rows:
        _revoke_pairing_row(
            pending_row,
            reason=CompanionPairingRevocationReason.PENDING_CANCELLED,
        )
        db.add(pending_row)

    companion_credential_secret = security.generate_companion_credential_secret()
    local_control_secret = security.generate_local_control_secret()
    local_control_secret_version = _next_secret_version(rows)
    pairing_session_id = uuid4().hex

    if active_row is not None:
        _revoke_pairing_row(
            active_row,
            reason=CompanionPairingRevocationReason.REPLACED,
        )
        db.add(active_row)

    pairing = CompanionPairing(
        user_id=row.user_id,
        pairing_session_id=pairing_session_id,
        status=CompanionPairingStatus.ACTIVE.value,
        api_protocol=row.api_protocol,
        api_host=row.api_host,
        api_port=row.api_port,
        paired_web_origin=row.paired_web_origin,
        tls_fingerprint=normalized_fingerprint,
        companion_credential_hash=security.hash_companion_credential_secret(
            companion_credential_secret
        ),
        local_control_secret_encrypted=encrypt_secret(local_control_secret),
        local_control_secret_version=local_control_secret_version,
        supersedes_pairing_session_id=None,
    )
    db.add(pairing)
    _set_request_terminal(
        row,
        status=CompanionPairingRequestStatus.COMPLETED,
        detail="Pairing completed successfully.",
        completed_pairing_session_id=pairing_session_id,
    )
    db.add(row)
    await db.commit()

    identity = companion_identity.get_backend_identity()
    return CompletedCompanionPairingRequest(
        api_protocol=row.api_protocol,
        api_host=row.api_host,
        api_port=row.api_port,
        paired_web_origin=row.paired_web_origin,
        companion_credential_secret=companion_credential_secret,
        local_control_secret=local_control_secret,
        local_control_secret_version=local_control_secret_version,
        backend_pairing_id=pairing_session_id,
        backend_identity_key_id=identity.key_id,
        backend_identity_public_key=identity.public_key,
    )


async def prepare_companion_pairing(
    db: AsyncSession,
    *,
    current_user: User,
    pairing_code: str,
    paired_web_origin: str,
    tls_fingerprint: str | None,
) -> PreparedCompanionPairingPayload:
    normalized_origin = _normalize_origin(paired_web_origin)
    if not normalized_origin:
        raise CompanionPairingStateError(
            "Pairing requests must include a valid Origin header.",
            status_code=400,
        )

    rows = await _load_pairings_for_user(db, current_user.id)
    active_rows, pending_rows, _ = _group_pairings(rows)

    if pending_rows:
        raise CompanionPairingStateError(
            "A previous Companion pairing attempt is still pending. Revoke the pairing and try again."
        )

    active_row = active_rows[0] if active_rows else None
    latest_live_version = _latest_live_secret_version(rows)
    if active_row and active_row.local_control_secret_version != latest_live_version:
        raise CompanionPairingStateError(
            "Companion pairing state is stale. Revoke the pairing and pair again."
        )

    api_protocol, api_host, api_port = _parse_api_origin(get_trusted_web_origin())
    companion_credential_secret = security.generate_companion_credential_secret()
    local_control_secret = security.generate_local_control_secret()
    pairing_session_id = uuid4().hex
    local_control_secret_version = _next_secret_version(rows)

    pairing = CompanionPairing(
        user_id=current_user.id,
        pairing_session_id=pairing_session_id,
        status=CompanionPairingStatus.PENDING.value,
        api_protocol=api_protocol,
        api_host=api_host,
        api_port=api_port,
        paired_web_origin=normalized_origin,
        tls_fingerprint=tls_fingerprint,
        companion_credential_hash=security.hash_companion_credential_secret(
            companion_credential_secret
        ),
        local_control_secret_encrypted=encrypt_secret(local_control_secret),
        local_control_secret_version=local_control_secret_version,
        supersedes_pairing_session_id=(
            active_row.pairing_session_id if active_row is not None else None
        ),
    )

    db.add(pairing)
    await db.commit()

    return PreparedCompanionPairingPayload(
        pairing_code=pairing_code,
        companion_credential_secret=companion_credential_secret,
        api_protocol=api_protocol,
        api_host=api_host,
        api_port=api_port,
        tls_fingerprint=tls_fingerprint,
        local_control_secret=local_control_secret,
        local_control_secret_version=local_control_secret_version,
        backend_pairing_id=pairing_session_id,
    )


async def exchange_companion_credential(
    db: AsyncSession,
    *,
    pairing_session_id: str,
    companion_credential_secret: str,
) -> CompanionCredentialExchangeResult:
    result = await db.execute(
        select(CompanionPairing).where(
            CompanionPairing.pairing_session_id == pairing_session_id
        )
    )
    pairing = result.scalar_one_or_none()
    if pairing is None:
        raise CompanionPairingStateError(
            "Companion pairing state is stale or revoked. Pair again from Nojoin."
        )

    if pairing.status == CompanionPairingStatus.REVOKED.value:
        raise CompanionPairingStateError(
            "Companion pairing was revoked. Pair again from Nojoin."
        )

    if not pairing.companion_credential_hash:
        raise CompanionPairingStateError(
            "Companion pairing state is incomplete. Revoke the pairing and pair again."
        )

    if not security.verify_companion_credential_secret(
        companion_credential_secret,
        pairing.companion_credential_hash,
    ):
        raise CompanionPairingStateError(
            "Companion pairing credential is invalid. Pair again from Nojoin.",
            status_code=401,
        )

    user_row = (
        await db.execute(
            select(User.id, User.username, User.is_active).where(User.id == pairing.user_id)
        )
    ).one_or_none()
    if user_row is None:
        raise CompanionPairingStateError(
            "Companion pairing user no longer exists. Pair again from Nojoin.",
            status_code=401,
        )
    user = CompanionExchangeUser(
        id=user_row[0],
        username=user_row[1],
        is_active=user_row[2],
    )
    if not user.is_active:
        raise CompanionPairingStateError(
            "Inactive user",
            status_code=400,
        )

    activated = pairing.status == CompanionPairingStatus.PENDING.value
    active_pairing = await finalize_companion_pairing(
        db,
        current_user=user,
        pairing_session_id=pairing_session_id,
    )

    return CompanionCredentialExchangeResult(
        user=user,
        pairing_session_id=active_pairing.pairing_session_id,
        activated=activated,
    )


async def finalize_companion_pairing(
    db: AsyncSession,
    *,
    current_user: User,
    pairing_session_id: str,
) -> CompanionPairing:
    rows = await _load_pairings_for_user(db, current_user.id)
    active_rows, pending_rows, _ = _group_pairings(rows)
    row_by_id = {row.pairing_session_id: row for row in rows}

    pairing = row_by_id.get(pairing_session_id)
    if pairing is None:
        raise CompanionPairingStateError(
            "Companion pairing state is stale or revoked. Pair again from Nojoin."
        )

    if pairing.status == CompanionPairingStatus.REVOKED.value:
        raise CompanionPairingStateError(
            "Companion pairing was revoked. Pair again from Nojoin."
        )

    latest_live_version = _latest_live_secret_version(rows)
    if pairing.local_control_secret_version != latest_live_version:
        raise CompanionPairingStateError(
            "Companion pairing state is stale or rotated. Pair again from Nojoin."
        )

    if pairing.status == CompanionPairingStatus.ACTIVE.value:
        if pending_rows:
            raise CompanionPairingStateError(
                "A newer Companion pairing is pending. Pair again from Nojoin."
            )
        return pairing

    if pairing.status != CompanionPairingStatus.PENDING.value:
        raise CompanionPairingStateError(
            "Companion pairing state is invalid. Pair again from Nojoin."
        )

    if len(pending_rows) != 1 or pending_rows[0].pairing_session_id != pairing_session_id:
        raise CompanionPairingStateError(
            "Companion pairing state is conflicting. Revoke the pairing and pair again."
        )

    active_row = active_rows[0] if active_rows else None
    if pairing.supersedes_pairing_session_id:
        if active_row is None or active_row.pairing_session_id != pairing.supersedes_pairing_session_id:
            raise CompanionPairingStateError(
                "Companion pairing replacement state is incomplete. Revoke the pairing and pair again."
            )
        active_row.status = CompanionPairingStatus.REVOKED.value
        active_row.companion_credential_hash = None
        active_row.local_control_secret_encrypted = None
        active_row.revoked_at = utc_now()
        active_row.revocation_reason = CompanionPairingRevocationReason.REPLACED.value
        db.add(active_row)
    elif active_row is not None:
        raise CompanionPairingStateError(
            "Companion pairing state is conflicting. Revoke the pairing and pair again."
        )

    pairing.status = CompanionPairingStatus.ACTIVE.value
    pairing.supersedes_pairing_session_id = None
    db.add(pairing)
    await db.commit()
    return pairing


async def get_active_companion_pairing_auth(
    db: AsyncSession,
    *,
    current_user: User,
) -> ActiveCompanionPairingAuth:
    rows = await _load_pairings_for_user(db, current_user.id)
    active_rows, pending_rows, _ = _group_pairings(rows)

    if pending_rows:
        raise CompanionPairingStateError(
            "A newer Companion pairing is pending. Pair again from Nojoin."
        )

    active_row = active_rows[0] if active_rows else None
    if active_row is None:
        raise CompanionPairingStateError(
            "Companion pairing is not active. Pair again from Nojoin."
        )

    latest_live_version = _latest_live_secret_version(rows)
    if active_row.local_control_secret_version != latest_live_version:
        raise CompanionPairingStateError(
            "Companion pairing state is stale or rotated. Pair again from Nojoin."
        )

    if not active_row.local_control_secret_encrypted:
        raise CompanionPairingStateError(
            "Companion pairing state is incomplete. Revoke the pairing and pair again."
        )

    return ActiveCompanionPairingAuth(
        pairing_session_id=active_row.pairing_session_id,
        paired_web_origin=active_row.paired_web_origin,
        local_control_secret=decrypt_secret(active_row.local_control_secret_encrypted),
        local_control_secret_version=active_row.local_control_secret_version,
    )


async def revoke_companion_pairings(
    db: AsyncSession,
    *,
    current_user: User,
) -> int:
    rows = await _load_pairings_for_user(db, current_user.id)
    revoked_count = 0
    changed = False

    for row in rows:
        if row.status == CompanionPairingStatus.REVOKED.value:
            if row.local_control_secret_encrypted is not None:
                row.local_control_secret_encrypted = None
                db.add(row)
                changed = True
            continue

        _revoke_pairing_row(
            row,
            reason=CompanionPairingRevocationReason.MANUAL_UNPAIR,
        )
        db.add(row)
        revoked_count += 1
        changed = True

    if changed:
        await db.commit()

    return revoked_count


async def cancel_pending_companion_pairings(
    db: AsyncSession,
    *,
    current_user: User,
) -> int:
    rows = await _load_pairings_for_user(db, current_user.id)
    requests = await _load_pairing_requests_for_user(db, current_user.id)
    cancelled_count = 0
    changed = False

    for request in requests:
        if _expire_request_if_needed(request):
            db.add(request)
            changed = True
            continue

        if _request_is_terminal(request):
            continue

        _set_request_terminal(
            request,
            status=CompanionPairingRequestStatus.CANCELLED,
            detail="Pairing request cancelled before approval.",
            failure_reason="cancelled",
        )
        db.add(request)
        cancelled_count += 1
        changed = True

    for row in rows:
        if row.status == CompanionPairingStatus.REVOKED.value:
            if row.local_control_secret_encrypted is not None:
                row.local_control_secret_encrypted = None
                db.add(row)
                changed = True
            continue

        if row.status != CompanionPairingStatus.PENDING.value:
            continue

        _revoke_pairing_row(
            row,
            reason=CompanionPairingRevocationReason.PENDING_CANCELLED,
        )
        db.add(row)
        cancelled_count += 1
        changed = True

    if changed:
        await db.commit()

    return cancelled_count