"""Calendar service package facade.

The calendar service was decomposed from a single 2700-line module into cohesive
submodules (BE-008 file-size gate). This ``__init__`` re-exports the full public
and test-imported surface so every existing import site -- API endpoints, the
Celery worker tasks, the push service, and the test suite -- keeps working
against ``backend.services.calendar_service`` unchanged. All implementation lives
in the submodules; this file is a thin aggregator only.
"""

from .config import (
    _load_connection_token_bundle,
    _prune_unreadable_connections,
    _reset_connection_requiring_reconnect,
    _reset_unreadable_provider_configuration,
    _serialise_provider_availability,
    get_provider_runtime_config,
    list_provider_statuses,
    update_provider_configuration,
)
from .constants import (
    ACCOUNT_REDIRECT_PATHS,
    ACCOUNT_REDIRECT_STATUSES,
    GOOGLE_AUTH_URL,
    GOOGLE_CALENDAR_LIST_URL,
    GOOGLE_EVENTS_URL_TEMPLATE,
    GOOGLE_SCOPE,
    GOOGLE_TOKEN_URL,
    GOOGLE_USERINFO_URL,
    HREF_URL_PATTERN,
    MEETING_URL_HOST_PRIORITY,
    MICROSOFT_COMMON_TENANT,
    MICROSOFT_EVENT_SELECT,
    MICROSOFT_GRAPH_URL,
    MICROSOFT_SCOPE,
    OAUTH_STATE_TTL_SECONDS,
    PLAIN_URL_PATTERN,
    PROVIDER_DISPLAY_NAMES,
    PROVIDER_ENV_KEYS,
    SYNC_WINDOW_MONTHS_BACK,
    SYNC_WINDOW_MONTHS_FORWARD,
    TRAILING_URL_PUNCTUATION,
    TRUSTED_MEETING_HOST_SUFFIXES,
    TRUSTED_MEETING_HOSTS,
)
from .dashboard import (
    _event_sort_key,
    _get_dashboard_recording_speaker_names,
    _get_dashboard_recording_tags,
    _get_recording_end,
    _iter_event_dates,
    _recording_status_value,
    _to_dashboard_event,
    _to_dashboard_recording,
    get_dashboard_summary,
)
from .models_dto import (
    IncrementalSyncResetRequired,
    ProviderCalendarRecord,
    ProviderEventRecord,
    ProviderEventSyncResult,
    ProviderIdentity,
    ProviderRuntimeConfig,
    TokenBundle,
    UnreadableCalendarConnectionState,
)
from .oauth import (
    _build_code_challenge,
    _exchange_google_code,
    _exchange_microsoft_code,
    _fetch_google_identity,
    _fetch_microsoft_identity,
    _get_access_token_for_connection,
    _oauth_state_fallback,
    _pop_oauth_state,
    _refresh_google_access_token,
    _refresh_microsoft_access_token,
    _save_oauth_state,
    start_authorisation,
)
from .persistence import (
    _active_push_connection_ids,
    _apply_incremental_calendar_events,
    _apply_provider_event_to_model,
    _build_calendar_event_model,
    _can_use_incremental_sync,
    _microsoft_calendar_has_partial_occurrence_artifacts,
    _refresh_connection_calendars,
    _replace_calendar_events,
    _serialise_connection,
    _serialise_connection_with_push,
    _serialise_source,
    _upsert_connection,
)
from .providers import (
    _build_google_events_query_params,
    _build_microsoft_calendar_view_url,
    _build_microsoft_delta_url,
    _get_microsoft_delta_cursor,
    _list_google_calendars,
    _list_microsoft_calendars,
    _list_microsoft_events,
    _normalise_google_event,
    _normalise_microsoft_event,
    _sync_google_events,
    _sync_microsoft_events,
)
from .sync import (
    _enqueue_push_channel_refresh,
    disconnect_connection,
    get_overview,
    handle_callback,
    refresh_connection_now,
    sync_all_connections,
    sync_connection_by_id,
    sync_connection_in_session,
    update_calendar_source_colour,
    update_connection_selection,
)
from .text_utils import (
    _add_months,
    _build_sync_window,
    _classify_sync_failure,
    _clean_error_text,
    _clean_url,
    _extract_error_text_from_payload,
    _extract_http_error_text,
    _extract_urls_from_text,
    _get_meeting_url_host,
    _get_microsoft_location_text,
    _is_partial_microsoft_occurrence,
    _is_trusted_meeting_url,
    _meeting_url_rank,
    _normalise_colour_value,
    _normalise_text,
    _parse_iso_datetime,
    _pick_preferred_meeting_url,
    _request_json,
    _start_of_month,
    _utc_now,
)
from .urls import (
    _build_account_redirect,
    _build_push_notification_url,
    _build_redirect_uri,
)

# HTTPException is re-exported so callers and tests can reference
# ``calendar_service.HTTPException``. It resolves to None when the web framework
# is absent (the Celery worker image ships no fastapi), preserving the original
# single module's import-time contract.
try:
    from fastapi import HTTPException
except ModuleNotFoundError:  # pragma: no cover - worker image has no fastapi
    HTTPException = None  # type: ignore[assignment, misc]

# Built dynamically so the exported surface cannot drift from the imports above.
__all__ = [name for name in globals() if not name.startswith("__")]
