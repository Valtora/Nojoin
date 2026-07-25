"""Turning database rows into archive records and back.

Covers redaction, schema adaptation for columns that have since been removed, the
calendar credential round trip (decrypted on the way out, re-encrypted on the way in, so
an archive restores onto an installation with a different encryption key), and the
parent-before-child ordering that tag hierarchies need.
"""

import logging
import os
from typing import Any, Dict, List, Type

from sqlmodel import SQLModel

from backend.core.backup.format import (
    CALENDAR_PROVIDER_ENV_KEYS,
    MICROSOFT_COMMON_TENANT,
)
from backend.core.backup.paths import (
    _build_backup_document_path,
    _build_backup_recording_audio_path,
)
from backend.core.backup.plans import _AudioPlan, _DocumentPlan
from backend.core.encryption import decrypt_secret, encrypt_secret
from backend.models.calendar import (
    CalendarConnection,
    CalendarProvider,
    CalendarProviderConfig,
)

logger = logging.getLogger(__name__)


def _redact_sensitive_data(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Recursively redact sensitive keys from a dictionary.
    """
    redacted = data.copy()
    for k, v in redacted.items():
        if isinstance(v, dict):
            redacted[k] = _redact_sensitive_data(v)
        elif isinstance(k, str) and (
            k.endswith("_key") or k.endswith("_token") or "password" in k
        ):
            if v:  # Only redact if there is a value
                redacted[k] = "REDACTED"
    return redacted


def _restore_redacted_sensitive_data(value: Any) -> Any:
    """
    Converts redacted placeholders back to null-like values on restore.
    """
    if isinstance(value, dict):
        return {
            key: _restore_redacted_sensitive_data(item) for key, item in value.items()
        }
    if isinstance(value, list):
        return [_restore_redacted_sensitive_data(item) for item in value]
    if value == "REDACTED":
        return None
    return value


def _adapt_record(model_cls: Type[SQLModel], data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Adapts a record dictionary to match the current model schema.
    Removes fields that no longer exist in the model.
    """
    # Gets current field names.
    if hasattr(model_cls, "model_fields"):
        current_fields = model_cls.model_fields.keys()
    else:
        current_fields = model_cls.__fields__.keys()

    # Filters data to only include fields that exist in the current model.
    return {k: v for k, v in data.items() if k in current_fields}


def _topological_sort(data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Sorts tags so that parents appear before children.
    """
    # 1. Build index and adjacency
    by_id = {item["id"]: item for item in data if "id" in item}
    children_map: Dict[Any, List[Dict[str, Any]]] = {}  # parent_id -> list of children
    roots = []

    for item in data:
        parent_id = item.get("parent_id")
        if parent_id and parent_id in by_id:
            if parent_id not in children_map:
                children_map[parent_id] = []
            children_map[parent_id].append(item)
        else:
            roots.append(item)

    # 2. Flatten
    sorted_items = []
    queue = list(roots)

    # Sort roots by ID to be deterministic
    queue.sort(key=lambda x: x.get("id", 0))

    while queue:
        node = queue.pop(0)
        sorted_items.append(node)

        node_id = node.get("id")
        if node_id in children_map:
            children = children_map[node_id]
            # Sort children by ID
            children.sort(key=lambda x: x.get("id", 0))
            # Append children to the end of the queue (BFS traversal).
            queue.extend(children)

    # Items in a cycle or with a missing parent are never reached by the BFS
    # above; append any leftovers so no row is dropped from the restore.
    if len(sorted_items) < len(data):
        processed_ids = {x.get("id") for x in sorted_items}
        for item in data:
            if item.get("id") not in processed_ids:
                sorted_items.append(item)

    return sorted_items


def _serialise_calendar_provider_configs(
    rows: List[CalendarProviderConfig],
) -> List[Dict[str, Any]]:
    serialised: List[Dict[str, Any]] = []
    rows_by_provider = {row.provider: row for row in rows}
    handled_providers: set[str] = set()

    for provider, env_keys in CALENDAR_PROVIDER_ENV_KEYS.items():
        row = rows_by_provider.get(provider)
        env_client_id = (
            os.getenv(env_keys["client_id"] or "") if env_keys["client_id"] else None
        )
        env_client_secret = (
            os.getenv(env_keys["client_secret"] or "")
            if env_keys["client_secret"]
            else None
        )
        env_tenant_id = (
            os.getenv(env_keys["tenant_id"] or "") if env_keys["tenant_id"] else None
        )

        if row is None:
            has_env_config = bool(
                env_client_id
                or env_client_secret
                or (provider == CalendarProvider.MICROSOFT.value and env_tenant_id)
            )
            if not has_env_config:
                continue

            serialised.append(
                {
                    "provider": provider,
                    "client_id": env_client_id,
                    "client_secret": env_client_secret,
                    "tenant_id": env_tenant_id
                    or (
                        MICROSOFT_COMMON_TENANT
                        if provider == CalendarProvider.MICROSOFT.value
                        else None
                    ),
                    "enabled": True,
                }
            )
            handled_providers.add(provider)
            continue

        row_data = row.model_dump(mode="json")
        decrypted_secret = decrypt_secret(row.client_secret_encrypted)

        if row.enabled is False:
            row_data["client_id"] = row.client_id
            row_data["tenant_id"] = row.tenant_id or (
                MICROSOFT_COMMON_TENANT
                if provider == CalendarProvider.MICROSOFT.value
                else None
            )
            row_data["client_secret"] = decrypted_secret
        else:
            row_data["client_id"] = row.client_id or env_client_id
            row_data["tenant_id"] = (
                row.tenant_id
                or env_tenant_id
                or (
                    MICROSOFT_COMMON_TENANT
                    if provider == CalendarProvider.MICROSOFT.value
                    else None
                )
            )
            row_data["client_secret"] = decrypted_secret or env_client_secret

        row_data.pop("client_secret_encrypted", None)
        serialised.append(row_data)
        handled_providers.add(provider)

    for row in rows:
        if row.provider in handled_providers:
            continue
        row_data = row.model_dump(mode="json")
        row_data["client_secret"] = decrypt_secret(row.client_secret_encrypted)
        row_data.pop("client_secret_encrypted", None)
        serialised.append(row_data)

    return serialised


def _serialise_calendar_connections(
    rows: List[CalendarConnection],
) -> List[Dict[str, Any]]:
    serialised: List[Dict[str, Any]] = []
    for row in rows:
        row_data = row.model_dump(mode="json")
        row_data["access_token"] = decrypt_secret(row.access_token_encrypted)
        row_data["refresh_token"] = decrypt_secret(row.refresh_token_encrypted)
        row_data.pop("access_token_encrypted", None)
        row_data.pop("refresh_token_encrypted", None)
        serialised.append(row_data)
    return serialised


def _prepare_calendar_provider_config_for_restore(
    data: Dict[str, Any],
) -> Dict[str, Any]:
    restored = data.copy()
    client_secret = restored.pop("client_secret", None)
    if "client_secret_encrypted" not in restored or client_secret is not None:
        stripped_secret = (
            client_secret.strip() if isinstance(client_secret, str) else client_secret
        )
        restored["client_secret_encrypted"] = (
            encrypt_secret(stripped_secret) if stripped_secret else None
        )

    if restored.get("provider") == CalendarProvider.GOOGLE.value:
        restored["tenant_id"] = None
    elif restored.get(
        "provider"
    ) == CalendarProvider.MICROSOFT.value and not restored.get("tenant_id"):
        restored["tenant_id"] = MICROSOFT_COMMON_TENANT

    return restored


def _prepare_calendar_connection_for_restore(
    data: Dict[str, Any],
) -> Dict[str, Any]:
    restored = data.copy()
    access_token = restored.pop("access_token", None)
    refresh_token = restored.pop("refresh_token", None)

    if "access_token_encrypted" not in restored or access_token is not None:
        stripped_access = (
            access_token.strip() if isinstance(access_token, str) else access_token
        )
        restored["access_token_encrypted"] = (
            encrypt_secret(stripped_access) if stripped_access else None
        )

    if "refresh_token_encrypted" not in restored or refresh_token is not None:
        stripped_refresh = (
            refresh_token.strip() if isinstance(refresh_token, str) else refresh_token
        )
        restored["refresh_token_encrypted"] = (
            encrypt_secret(stripped_refresh) if stripped_refresh else None
        )

    return restored


def _serialise_backup_table_rows(
    table_name: str,
    items: List[SQLModel],
    audio_plan: _AudioPlan | None = None,
    document_plan: _DocumentPlan | None = None,
) -> List[Dict[str, Any]]:
    if table_name == "calendar_provider_configs":
        data = _serialise_calendar_provider_configs(items)
    elif table_name == "calendar_connections":
        data = _serialise_calendar_connections(items)
    else:
        data = [item.model_dump(mode="json") for item in items]

    rewriter = _ROW_REWRITERS.get(table_name)
    if rewriter is not None:
        rewriter(data, audio_plan, document_plan)

    return data


def _rewrite_recording_rows(
    data: List[Dict[str, Any]],
    audio_plan: _AudioPlan | None,
    _document_plan: _DocumentPlan | None,
) -> None:
    arcnames = audio_plan.arcname_by_audio_path if audio_plan else {}
    for item in data:
        original_audio_path = item.get("audio_path")
        if original_audio_path:
            # The archived member path is the row's audio_path inside the backup, which
            # is what lets a variable archive extension round-trip without a separate
            # manifest. A recording whose audio could not be found still gets a
            # plausible path so the row itself survives the restore.
            item["audio_path"] = arcnames.get(
                original_audio_path
            ) or _build_backup_recording_audio_path(
                original_audio_path,
                os.path.splitext(original_audio_path)[1].lower() or ".opus",
            )
            # Recomputed from the extracted file on restore, since a re-encoded archive
            # makes the source system's byte count wrong.
            item["file_size_bytes"] = None

        # Proxy files are not backed up directly; they are regenerated after restore.
        item["proxy_path"] = None


def _rewrite_document_rows(
    data: List[Dict[str, Any]],
    _audio_plan: _AudioPlan | None,
    document_plan: _DocumentPlan | None,
) -> None:
    arcnames = document_plan.arcname_by_file_path if document_plan else {}
    for item in data:
        original_file_path = item.get("file_path")
        if original_file_path:
            item["file_path"] = arcnames.get(
                original_file_path
            ) or _build_backup_document_path(original_file_path)


def _rewrite_user_rows(
    data: List[Dict[str, Any]],
    _audio_plan: _AudioPlan | None,
    _document_plan: _DocumentPlan | None,
) -> None:
    for item in data:
        if item.get("settings"):
            item["settings"] = _redact_sensitive_data(item["settings"])


# Per-table row rewrites applied after the generic dump. Only these three tables carry
# values that mean something different inside an archive than they do in the database.
_ROW_REWRITERS = {
    "recordings": _rewrite_recording_rows,
    "documents": _rewrite_document_rows,
    "users": _rewrite_user_rows,
}
