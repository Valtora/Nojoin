"""Static provider tables, endpoint URLs, scopes, and matching patterns.

Pure module-level constants shared across the calendar service package. This is
a dependency leaf: it must not import from sibling submodules.
"""

from __future__ import annotations

import re
from urllib.parse import urlencode

from backend.models.calendar import CalendarProvider

GOOGLE_SCOPE = "openid email profile https://www.googleapis.com/auth/calendar.readonly"
MICROSOFT_SCOPE = "openid profile email offline_access User.Read Calendars.Read"
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
GOOGLE_CALENDAR_LIST_URL = (
    "https://www.googleapis.com/calendar/v3/users/me/calendarList"
)
GOOGLE_EVENTS_URL_TEMPLATE = (
    "https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events"
)
MICROSOFT_GRAPH_URL = "https://graph.microsoft.com/v1.0"
MICROSOFT_COMMON_TENANT = "common"
OAUTH_STATE_TTL_SECONDS = 10 * 60
# OAuth ``error`` codes (RFC 6749 section 5.2 and OpenID Connect) with which a
# token endpoint refuses a stored refresh token for good: revoked, expired,
# password changed, or new consent or MFA needed. Only signing in again fixes
# them. Microsoft sends its AADSTS refusals (50076, 50173, 65001, 70008,
# 700082) as ``invalid_grant`` or ``interaction_required``, with the AADSTS
# number in ``error_description`` and ``error_codes``.
OAUTH_RECONNECT_ERROR_CODES = frozenset(
    {"invalid_grant", "interaction_required", "consent_required", "login_required"}
)
OAUTH_RECONNECT_STATUS_CODES = frozenset({400, 401})
SYNC_WINDOW_MONTHS_BACK = 12
SYNC_WINDOW_MONTHS_FORWARD = 12

PROVIDER_DISPLAY_NAMES = {
    CalendarProvider.GOOGLE.value: "Google",
    CalendarProvider.MICROSOFT.value: "Microsoft",
}

PROVIDER_ENV_KEYS = {
    CalendarProvider.GOOGLE.value: {
        "client_id": "GOOGLE_OAUTH_CLIENT_ID",
        "client_secret": "GOOGLE_OAUTH_CLIENT_SECRET",
        "tenant_id": None,
    },
    CalendarProvider.MICROSOFT.value: {
        "client_id": "MICROSOFT_OAUTH_CLIENT_ID",
        "client_secret": "MICROSOFT_OAUTH_CLIENT_SECRET",
        "tenant_id": "MICROSOFT_OAUTH_TENANT_ID",
    },
}

ACCOUNT_REDIRECT_STATUSES = (
    "success",
    "error",
    "config-error",
    "cancelled",
    "tenant-config-error",
)
# The callback lands on the page that holds the Calendars card. The settings
# index turns a legacy ``?tab=`` link into a category route and drops every other
# query parameter, so pointing at ``/settings?tab=...`` loses the result.
ACCOUNT_REDIRECT_PATHS = {
    provider: {
        status_value: f"/settings/integrations?{urlencode({'calendar': status_value, 'provider': provider})}"
        for status_value in ACCOUNT_REDIRECT_STATUSES
    }
    for provider in PROVIDER_DISPLAY_NAMES
}

TRAILING_URL_PUNCTUATION = ".,);]>"
HREF_URL_PATTERN = re.compile(r'href=["\'](https?://[^"\']+)["\']', re.IGNORECASE)
PLAIN_URL_PATTERN = re.compile(r'https?://[^\s<>"\']+')
MEETING_URL_HOST_PRIORITY = (
    "teams.microsoft.com",
    "meet.google.com",
    ".zoom.us",
    ".webex.com",
    ".gotomeeting.com",
    ".bluejeans.com",
    ".whereby.com",
    ".ringcentral.com",
    "meet.jit.si",
)
TRUSTED_MEETING_HOSTS = {
    "meet.google.com",
    "teams.microsoft.com",
    "teams.live.com",
    "zoom.us",
}
TRUSTED_MEETING_HOST_SUFFIXES = (".zoom.us",)
MICROSOFT_EVENT_SELECT = ",".join(
    [
        "id",
        "subject",
        "type",
        "seriesMasterId",
        "isAllDay",
        "isCancelled",
        "start",
        "end",
        "lastModifiedDateTime",
        "webLink",
        "location",
        "locations",
        "body",
        "bodyPreview",
        "onlineMeeting",
        "onlineMeetingUrl",
        "attendees",
    ]
)
