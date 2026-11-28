# nojoin-proton helper protocol

`nojoin-proton` is the small Go program Nojoin runs once per Proton Calendar operation. It speaks
Proton's private API and iCalendar; the Python backend speaks only Nojoin's provider-neutral model and
never sees a Proton card or key. This file is the contract between the two. Any change to a request or
result shape changes this file in the same commit.

Protocol version: **1**.

## Process rules

- **One request, one response.** The caller writes one JSON object to stdin and closes it. The helper
  writes one JSON object to stdout and exits 0, whether the operation succeeded or failed. A non-zero
  exit, or stdout that is not one JSON object, means the helper crashed: treat it as
  `provider_unavailable`, with `outcome_unknown` true for a write operation.
- **Limits.** The request may be at most 4 MiB. The response is at most 32 MiB; if a result would be
  larger, the helper answers `output_too_large` instead. `list_events` windows are capped at 93 days,
  which keeps a response well inside the limit.
- **Secrets on stdin only.** Tokens, the key passphrase and passwords travel only in the request.
  The helper reads no secret from argv or the environment, writes no files, and never logs a secret.
- **Environment.** Start it with an allowlisted environment: `HTTPS_PROXY`, `NO_PROXY` and
  `SSL_CERT_FILE`, nothing else. The helper reads no other variable. Time zone data is compiled in.
- **stderr** carries at most one short line naming the error code when an operation fails. It never
  holds request data. Keep only the code from it.
- **Timeouts are the caller's.** Kill the whole process group when the operation's own budget runs out
  (the design uses 30 s for a write and 120 s for a sync chunk). `client.timeout_ms` lets the helper
  stop a little earlier and report cleanly instead; set it to the kill timeout minus a margin.
- **Client identity.** Every request to Proton carries `x-pm-appversion: Other` (the constant
  `AppVersion` in `internal/papi`). The caller cannot change it. If Proton refuses it, the helper
  answers `provider_rejected` with `details.reason` `app_version_rejected`, and Nojoin must not fall
  back to another identity (maintainer decision Q5).
- **Network.** The helper contacts only the Proton API base URL. Nothing else.

## Request

```json
{
  "v": 1,
  "op": "update_event",
  "session": {"uid": "...", "access_token": "...", "refresh_token": "...", "key_passphrase": "..."},
  "client": {"user_agent": "Nojoin/3.2.0", "timeout_ms": 28000, "api_base_url": null},
  "args": {}
}
```

- `v` must be 1. `op` is one of the operations below. Unknown fields anywhere in the request are refused
  with `invalid_request`, so a typo fails loudly.
- `session` carries what the operation needs and nothing more is required:

  | Operation | `uid` | `access_token` | `refresh_token` | `key_passphrase` |
  | --- | --- | --- | --- | --- |
  | `version`, `validate`, `login_start` | | | | |
  | `login_2fa`, `login_unlock`, `logout`, `list_calendars` | yes | yes | | |
  | `refresh` | yes | | yes | |
  | every other operation | yes | yes | | yes |

- `client.user_agent` is sent as `User-Agent`; it defaults to `Nojoin`. `client.timeout_ms` is the
  helper's own deadline for the whole operation (default 25000, at most 600000). `client.api_base_url`
  replaces `https://mail.proton.me/api`; set it only from a test or operator setting, never from user
  input, because the session's secrets go to whatever host it names.

## Response

```json
{"v": 1, "ok": true, "result": {}}
{"v": 1, "ok": false, "error": {"code": "conflict", "message": "...", "retryable": false,
  "provider_code": null, "http_status": null, "field": null, "details": {}}}
```

- `message` is safe to log and to show: it holds no secret, and provider text in it has addresses
  masked. `details` may hold addresses (attendees, the account), so log only `code`.
- `provider_code` is Proton's numeric `Code` when Proton gave one; `http_status` its HTTP status.
- `field` names the argument at fault for `invalid_argument` and `invalid_time`, using the names in
  this file (for example `event.recurrence_rule`).

## Tokens

- Only `refresh` and the `login_*` operations return tokens. No other operation refreshes or rotates
  anything.
- When Proton refuses the access token (HTTP 401) **before anything was written**, the helper answers
  `access_token_expired`. The caller then takes the connection lock, runs `refresh`, commits the rotated
  tokens, and retries the operation once.
- When the refusal comes after a write landed (during the read-back), the write is reported as done
  with `verified` false and the warning `read_back_failed`; the caller must not retry it.
- Proton's refresh tokens are single use. `refresh` sends the refresh token once and waits for the
  answer even past `client.timeout_ms` (at most 90 s), so give it a kill timeout above that. If the process is killed
  mid-refresh, the old refresh token may already be spent: retry `refresh` once with it, and mark the
  connection for reconnection if that is refused.

## Error codes

Each helper code maps onto the error taxonomy in section 11 of the design. The first group never reaches
an assistant: the caller handles it.

| Helper code | Retry | Caller's mapping |
| --- | --- | --- |
| `access_token_expired` | after `refresh` | refresh, then retry once (above) |
| `invalid_request` | no | a Nojoin bug: log the code and fail the call as `provider_unavailable` |
| `output_too_large` | no | `provider_unavailable`; narrow the window |
| `login_failed` | no | connect flow: wrong username or password (Proton 8002) |
| `human_verification_required` | after the check | connect flow: `details` has `methods`, `token`, `verify_url` |
| `two_factor_failed` | yes | connect flow: wrong code |
| `two_factor_unsupported` | no | connect flow: security-key-only account |
| `wrong_mailbox_password` | yes | connect flow: the password did not open the account's keys |
| `invalid_argument` | no | `invalid_argument` (`field`) |
| `invalid_time` | no | `invalid_time` (`field`); also Proton 2001 on a time or `TZID` |
| `unsupported` | no | `unsupported` (`details.hint`) |
| `not_found` | no | `not_found`; Proton 2061, 2501, HTTP 404 and 410 |
| `not_writable` | no | `not_writable`, `details.reason` `calendar_read_only` |
| `not_organiser` | no | `not_organiser` |
| `not_attendee` | no | `not_attendee` |
| `conflict` | after re-reading | `conflict`, `details.current_revision` |
| `auth_expired` | no | `auth_expired`; mark the connection `reauthorisation_required`. `details.reason`: `refresh_refused`, `session_revoked`, `key_passphrase_invalid`, `scope_lost`, `human_verification_required` |
| `rate_limited` | yes, after the delay | `rate_limited`, `details.retry_after_seconds` |
| `provider_unavailable` | yes | `provider_unavailable`; `details.outcome_unknown` true when a write was sent and no answer came back |
| `provider_rejected` | no | `provider_rejected`, `provider_code`; `details.reason` `app_version_rejected` for Proton 5002, 5003 and 2064 |

Proton's batch code 1001 means the batch was accepted; the per-entry code inside it is the verdict, and a
pure delete answers 1001 with no entries, which is success. When some entries of a batch landed and
another was refused, the error carries `details.partial` true: read the event again before anything
else.

## Shared shapes

### Time

A point in time, as an input or an output:

```json
{"date_time": "2026-12-01T14:00:00Z", "time_zone": "Europe/London"}
{"date": "2026-12-24"}
```

- On input `date_time` must carry an offset or `Z`: the caller resolves wall-clock times (design
  section 3.3). `time_zone` is the IANA zone the event is written in; the helper writes
  `DTSTART;TZID=<zone>:<local time>`, or `DTSTART:<UTC>Z` when the zone is `UTC`. Floating times are
  never written.
- `date` is an all-day date. An all-day `end` is exclusive.

### Event fields (input)

```json
{
  "title": "Hold: Design review",
  "description": "Agenda to follow.\nJoin: https://meet.example.com/abc-defg",
  "location": null,
  "url": "https://meet.example.com/abc-defg",
  "all_day": false,
  "start": {"date_time": "2026-12-01T14:00:00Z", "time_zone": "Europe/London"},
  "end": {"date_time": "2026-12-01T15:00:00Z", "time_zone": "Europe/London"},
  "hold": true,
  "show_as": "busy",
  "recurrence_rule": null,
  "attendees": [{"email": "bob@example.org", "name": "Bob Example", "role": "required"}]
}
```

- `title` is written exactly as given. **The `Hold: ` title prefix is the caller's job**: Python adds it
  when it makes a hold and strips it when it confirms one (maintainer decision Q3). The helper's own
  record of a hold is `hold`.
- `hold` true writes `STATUS:CONFIRMED` plus `X-NOJOIN-HOLD:TRUE` in the encrypted calendar card, the
  organiser-only part that guests never receive. `hold` false removes the property. `STATUS:TENTATIVE`
  is never written, because Proton has been measured refusing it.
- `show_as` `busy` writes `TRANSP:OPAQUE`, `free` writes `TRANSP:TRANSPARENT`. Holds stay busy unless
  the caller says otherwise.
- `url` is written as the iCalendar `URL` property. Adding a `Join:` line to the description is the
  caller's job.
- `role` is `required`, `optional` or `information` (`REQ-PARTICIPANT`, `OPT-PARTICIPANT`,
  `NON-PARTICIPANT`). The user's own address is never written as an attendee. The organiser is always
  the calendar member's own address.
- `recurrence_rule` is an RRULE value without the `RRULE:` prefix. The limits are those of Proton's web
  client (`getIsRruleSupported`): `FREQ` DAILY, WEEKLY, MONTHLY or YEARLY; `COUNT` at most 49; `UNTIL`
  no later than 2037-12-31; not both; parts allowed per frequency as the web client allows them;
  MONTHLY with one `BYDAY` and a `BYSETPOS` in -1, 1, 2, 3, 4 or one `BYMONTHDAY`; YEARLY `BYMONTHDAY`
  only with `BYMONTH`; `INTERVAL` within the web client's maxima. The series' start must be its first
  occurrence. A date `UNTIL` on a timed series is rewritten as the end of that day in the event's zone,
  in UTC.
- Field limits: `title` 255 characters, `description` 3000, `location` 255, `uid` 191 bytes,
  `attendees` 100.

### Window

```json
{"start": "2026-11-01T00:00:00Z", "end": "2027-01-31T00:00:00Z"}
```

Occurrences are listed when they overlap `[start, end)`. Series are expanded inside the window only.
Where a window is optional it defaults to one month before now until 24 months after, Nojoin's sync
range.

### Target

```json
{"uid": "abc123@nojoin", "recurrence_id": "20261201T140000Z"}
```

Names an event by its iCalendar `UID`, and one occurrence of a series by `recurrence_id`: the
occurrence's original start in UTC basic format (`YYYYMMDDTHHMMSSZ`), or `YYYYMMDD` for an all-day
series. It is the part after `#` in a record's `provider_event_id`. `recurrence_id` is null for a
single event or a whole series.

### Event record (output)

Every operation that returns events returns occurrences, one record per row Nojoin stores:

```json
{
  "provider_event_id": "abc123@nojoin#20261201T140000Z",
  "uid": "abc123@nojoin",
  "title": "Hold: Design review",
  "description": "Agenda to follow.",
  "location": null,
  "meeting_url": "https://meet.example.com/abc-defg",
  "url": "https://meet.example.com/abc-defg",
  "is_all_day": false,
  "starts_at": "2026-12-01T14:00:00Z",
  "ends_at": "2026-12-01T15:00:00Z",
  "start_date": null,
  "end_date": null,
  "start_time_zone": "Europe/London",
  "end_time_zone": "Europe/London",
  "status": "tentative",
  "is_hold": true,
  "show_as": "busy",
  "organiser": {"name": "Alice Example", "email": "alice@example.com", "is_self": true},
  "is_organiser": true,
  "my_response": null,
  "attendees": [
    {"name": "Bob Example", "email": "bob@example.org", "response": "needs_action",
     "role": "required", "is_self": false, "token": "4f1c..."}
  ],
  "attendee_count": 1,
  "series_key": "abc123@nojoin",
  "recurrence_rule": "FREQ=WEEKLY;BYDAY=TU;COUNT=10",
  "occurrence_type": "occurrence",
  "original_start": "2026-12-01T14:00:00Z",
  "original_start_date": null,
  "sequence": 0,
  "revision": "p1:1795860302:0",
  "created_at": "2026-11-28T10:05:02Z",
  "updated_at": "2026-11-28T10:05:02Z",
  "decrypt_warnings": [],
  "provider_data": {
    "calendar_id": "...", "event_id": "...", "master_event_id": "...", "shared_event_id": "...",
    "attendee_ids": {"4f1c...": "..."}, "is_proton_proton_invite": false,
    "is_personal_single_edit": false
  }
}
```

- `provider_event_id` is `{UID}` for a single event and `{UID}#{recurrence id}` for an occurrence of a
  series (design section 4.1), so an occurrence keeps its id when it is first edited on its own.
- Timed events have `starts_at` and `ends_at` (UTC, RFC 3339 with `Z`) and null dates. All-day events
  have `start_date` and an exclusive `end_date` and null `starts_at`/`ends_at`.
- `start_time_zone` is the event's own `TZID` (`UTC` for a UTC time); null for all-day events.
- `status` is `confirmed`, `tentative` or `cancelled`. `is_hold` is true when `X-NOJOIN-HOLD:TRUE` is in
  any card or the event carries `STATUS:TENTATIVE` (an imported event); a hold's `status` is
  `tentative`.
- `meeting_url` is Proton's conference URL (`X-PM-CONFERENCE-URL`) when there is one, else `URL`.
  Proton's conference block is cut out of `description`, as Proton's apps do.
- `organiser.is_self` and `attendees[].is_self` compare canonical addresses with every address on the
  account. `my_response` is the user's own answer to someone else's invitation, from Proton's clear
  attendee list; null on the user's own events. `response` is `accepted`, `declined`, `tentative` or
  `needs_action`.
- `occurrence_type` is `single`, `occurrence` (expanded from the series) or `exception` (an occurrence
  with its own row at Proton). `series_key` is the `UID` for any occurrence of a series and null for a
  single event. `recurrence_rule` is on every occurrence of a series.
- `revision` is `p1:{ModifyTime}:{SEQUENCE}` of the row that was read: the exception row for an
  occurrence that has one, else the master. Pass it back as `expected_revision`.
- `decrypt_warnings` lists cards that could not be read (`shared_encrypted`, `calendar_encrypted`,
  `attendees`), signatures that could not be checked (`signature_unverified`, for events signed by
  someone else), and `recurrence_unreadable` for a rule the helper cannot expand, in which case only
  the series' first occurrence is returned. A record is still returned with what could be read.
- `provider_data` is for the caller to store and hand back unchanged; it is not part of the neutral
  model.

### Write result

`create_event`, `update_event`, `cancel_event` and `respond` return:

```json
{
  "written": true,
  "verified": true,
  "events": [],
  "deleted_keys": [],
  "before": [],
  "revision": "p1:1795860710:1",
  "warnings": [],
  "stats": {"occurrences": 10, "exceptions_removed": 0},
  "itip": {"ics": "BEGIN:VEVENT\r\n...\r\nEND:VEVENT", "uid": "abc123@nojoin", "sequence": 1}
}
```

- `written` false means nothing changed at Proton: an update whose patch matched the event, or a
  response equal to the current one. `events` then holds the current state.
- `verified` true when `events` was read back from Proton after the write. False only when the write
  landed and the read-back failed; `warnings` then holds `read_back_failed` and `events` is built from
  what was sent.
- `events` are the rows to upsert, expanded inside the request's `window`. `deleted_keys` are
  `provider_event_id`s to delete.
- `before` is the state of the target before the write (for the change log; for a cancel it is the
  whole event as it was).
- `warnings` are short codes: `read_back_failed`, `attendee_keys_unavailable:<n>` (Proton attendees
  whose keys could not be read, so they were not handed the event's key), `exceptions_removed:<n>` and
  `personal_part_not_updated`.
- `itip` is an opaque iCalendar `VEVENT` of what was written, for `send_itip`. Pass it back unchanged.

## Operations

### `version`

No session, no network. Result:

```json
{"helper": "nojoin-proton", "protocol": 1, "go_version": "go1.27.4", "app_version": "Other",
 "operations": ["version", "login_start", "..."]}
```

The release smoke runs this in both images.

### `login_start`

Args: `{"username": "alice@example.com", "password": "...", "human_verification": null}`.
`human_verification` is `{"token": "...", "type": "captcha"}` when the user has finished Proton's check.

Opens an anonymous session (`POST /auth/v4/sessions`), reads the auth info, checks the modulus
signature, sends the SRP proof and checks the server proof. Result:

```json
{
  "session": {"uid": "...", "access_token": "...", "refresh_token": "...", "scopes": ["..."],
              "expires_in": 86400},
  "user_id": "...",
  "password_mode": 1,
  "two_factor": {"required": true, "totp": true, "fido2": false},
  "next": "two_factor"
}
```

- `next` is `two_factor` when a TOTP code is needed, else `unlock`.
- `human_verification_required` carries `details.methods`, `details.token` and `details.verify_url`
  (`https://verify.proton.me/?methods=captcha&token=...`) when `captcha` is offered; when it is not,
  `details.verify_url` is null.
- An account whose only second factor is a security key fails with `two_factor_unsupported`; the
  helper ends that partial session first.

### `login_2fa`

Args: `{"code": "123456"}`. Sends `POST /auth/v4/2fa`. Result:
`{"session": {...same tokens, "scopes": [...]}, "next": "unlock"}`. A wrong code is
`two_factor_failed`.

### `login_unlock`

Args: `{"username": "...", "password": "...", "mailbox_password": null}`. `mailbox_password` is required
when `password_mode` was 2.

Reads the user, addresses and key salts, derives the salted key passphrase, and checks it by decrypting
the user key and the address keys with it. When Proton refuses the salts for scope (9101), the helper gains the
scope with an SRP proof of `password` (`PUT /core/v4/users/unlock`), reads them and locks the scope
again. Then it reads calendar settings and the calendars. Result:

```json
{
  "session": {"uid": "...", "access_token": "...", "refresh_token": "...", "key_passphrase": "..."},
  "user": {"id": "...", "name": "alice", "display_name": "Alice Example", "email": "alice@example.com"},
  "addresses": [{"id": "...", "email": "alice@example.com", "display_name": "Alice Example",
                 "status": "enabled", "keys_unlocked": true}],
  "settings": {"default_calendar_id": "...", "primary_time_zone": "Europe/London"},
  "calendars": [],
  "warnings": []
}
```

`calendars` has the shape `list_calendars` returns. `session.refresh_token` is echoed when the request
carried it. A passphrase that opens no address key is `wrong_mailbox_password`. `warnings` holds
`address_keys_locked` when an enabled address's keys did not open (others did).

### `refresh`

Args: `{}`. Sends `POST /auth/v4/refresh` once. Result:
`{"session": {"uid": "...", "access_token": "...", "refresh_token": "...", "expires_in": 86400,
"scopes": [...]}}`. Commit both tokens before anything else. A refused refresh token is `auth_expired`
with `details.reason` `refresh_refused`.

### `logout`

Args: `{}`. Sends `DELETE /auth/v4`. Result: `{"ended": true}`, or `{"ended": false}` when Proton
already considered the session over (401).

### `list_calendars`

Args: `{}`. Reads `GET /calendar/v1` and `GET /settings/calendar`. Result:

```json
{
  "settings": {"default_calendar_id": "...", "primary_time_zone": "Europe/London"},
  "calendars": [
    {"id": "...", "name": "Personal", "description": "", "colour": "#5252cc", "type": "personal",
     "member_id": "...", "address_id": "...", "email": "alice@example.com", "permissions": 127,
     "writable": true, "read_only_reason": null, "is_default": true, "display": true,
     "time_zone": "Europe/London", "flags": 1}
  ]
}
```

`type` is `personal`, `subscribed` or `holidays`. `writable` is true when the member has the WRITE
permission bit and the calendar is personal; otherwise `read_only_reason` is `calendar_read_only`.
`time_zone` is the account's primary zone (Proton has no zone per calendar).

### `list_events`

Args: `{"calendar_id": "...", "window": {...}, "include_cursor": false}`. The window must be at most 93
days. With `include_cursor` the helper first reads `modelevents/latest`, so a full sync takes the cursor
before its first chunk.

Reads the four `Type` buckets with `PageSize` 100, paging while `More` is 1, the window padded a day each
side, deduplicated by row ID; decrypts each row, expands series in the event's own zone, applies
`EXDATE`s and lets exception rows replace their occurrences. Result:

```json
{"events": [], "cursor": "...", "stats": {"rows": 12, "occurrences": 31, "undecryptable": 0}}
```

`cursor` is null without `include_cursor`. A row that cannot be decrypted at all is counted and skipped.

### `changes`

Args: `{"calendar_id": "...", "cursor": "...", "window": {...}}`. Reads `modelevents/{cursor}` (up to 10
pages while `More` is 1). Result:

```json
{
  "cursor": "...",
  "more": false,
  "refresh_required": false,
  "series": [{"uid": "...", "events": []}],
  "deleted_event_ids": ["..."]
}
```

- `series` holds every changed `UID`, re-read by `UID` and expanded inside `window`. Replace every stored
  row of that `series_key` (or that single event) with `events`; an empty list means the event is gone.
- `deleted_event_ids` are Proton row IDs that were deleted. Match them against `provider_data.event_id`
  and `master_event_id`; when one was an exception row of a series that still exists, run `get_event`
  for that `UID` to restore its occurrence.
- `refresh_required` true (Proton's `Refresh` flag, or a cursor Proton no longer knows) means do a full
  fetch and reconcile. `more` true means call again with the new `cursor`.
- The response shape of `modelevents` comes from Proton's web client and has not been checked against a
  live account.

### `get_event`

Args: `{"calendar_id": "...", "target": {...}, "window": null}`. With `recurrence_id` the result is that
one occurrence; without it, the single event or every occurrence of the series inside `window`. Result:
`{"events": []}`. An event that no longer exists is `not_found`.

### `create_event`

Args:

```json
{"calendar_id": "...", "uid": "abc123@nojoin", "event": {...event fields...},
 "share_with_attendees": false, "window": null}
```

- `uid` is the caller's (for a keyed create, the derived UUID5 of design section 9.1). Proton refuses
  a second event with the same `UID` (2500), which the helper reports as `conflict` with
  `details.reason` `uid_exists`.
- Writes one row through `PUT /calendar/v1/{id}/events/sync` with fresh session keys, the cards split as
  Proton's web client splits them, and `SEQUENCE:0`.
- With `share_with_attendees` false (the default) no attendee's keys are looked up and the body carries
  no `AddedProtonAttendees`; attendees are written into the encrypted attendee card and the clear list
  only. With it true, each attendee's public keys are looked up (`GET /core/v4/keys`) and Proton
  attendees get the shared session key wrapped to their key in `AddedProtonAttendees`. No write
  operation touches a mail endpoint: invitations are `send_itip` alone.
- Result: the write result.

### `update_event`

Args:

```json
{
  "calendar_id": "...",
  "target": {"uid": "abc123@nojoin", "recurrence_id": null},
  "scope": "single",
  "patch": {"title": "Design review", "hold": false},
  "share_with_attendees": false,
  "share_with": [],
  "expected_revision": "p1:1795860302:0",
  "window": null
}
```

- `scope` is `single` (a single event; `recurrence_id` null), `occurrence` (one occurrence;
  `recurrence_id` set) or `series` (the whole series; `recurrence_id` names the occurrence whose new
  times `start` and `end` give, or null for the first).
- `patch` keys: `title`, `description`, `location`, `url`, `start`, `end`, `all_day`, `hold`, `show_as`,
  `recurrence_rule`, `add_attendees`, `remove_attendees` (addresses). A key that is absent is unchanged;
  `null` clears `description`, `location`, `url` or `recurrence_rule`.
- With `expected_revision`, the helper reads the target fresh and answers `conflict` when its revision
  moved. Without it the patch is applied to Proton's current version, so a concurrent change to another
  field survives.
- The decrypted cards are patched in place: every property the patch does not touch is kept verbatim,
  unknown ones included, and the existing session keys are reused (no key packets on an update).
  `SEQUENCE` goes up on a change of time, recurrence, hold status or attendees, and is written bare.
- `occurrence` with no exception row creates one with the master's properties patched, the same `UID`,
  a `RECURRENCE-ID` in the master's form, `SEQUENCE` equal to the master's, and the master's shared
  session key wrapped again to the calendar key. With an exception row, that row is updated.
- `series`: patches the master. A change of time keeps the date of the named occurrence (another date
  is `unsupported`) and moves the master's time of day and length by the same amount, in the event's
  zone. A change of time or rule deletes every exception row in the same batch
  (`stats.exceptions_removed`). Clearing the rule keeps the first occurrence as a single event.
- `recurrence_rule` is refused on `occurrence`. Setting it on `single` turns the event into a series.
- Refused with `not_organiser` for an event the user does not organise. A patch that changes nothing
  returns `written` false.
- With `share_with_attendees` true, Proton attendees the patch adds get key packets, and so do the
  attendees named in `share_with`: the caller lists there the ones its own records say were never
  invited (every attendee of a hold being confirmed, for instance). Removed attendees go in
  `RemovedAttendeeAddresses`. With it false, neither field is sent and `share_with` is ignored.
- `series` content changes (title, description, location, `url`, `show_as`, hold) also reach each
  exception row that still had the master's old value, so an occurrence edited on its own keeps its own
  edit. Attendee additions and removals apply to every exception. When the master's `SEQUENCE` goes up
  and exceptions stay, each exception is raised to at least the master's `SEQUENCE` in the same batch.
- `hold` true on an event that has no private calendar part (some events made in Proton's own apps
  carry no calendar key packet, and an update cannot add one) is `unsupported`.

### `cancel_event`

Args: `{"calendar_id": "...", "target": {...}, "scope": "single", "expected_revision": null,
"window": null}`.

- `single` deletes the row. `occurrence` adds an `EXDATE` to the master (in the shared signed card and
  the calendar signed card, as the web client routes it) and deletes that occurrence's exception row in
  the same batch. `series` deletes the master and every row with its `UID` in one batch.
- Refused with `not_organiser` for someone else's invitation.
- `before` holds the event's last state, `deleted_keys` the removed rows, and for `occurrence` `events`
  holds the series as it now is. Other exception rows are raised to the master's new `SEQUENCE` in
  the same batch.
- `itip` is the event as it was (for `occurrence`, that occurrence with its `RECURRENCE-ID`), ready for a
  `CANCEL`.

### `respond`

Args: `{"calendar_id": "...", "target": {"uid": "...", "recurrence_id": null},
"response": "accepted", "expected_revision": null, "window": null}`.

- For an event the user attends (`IsOrganizer` 0). The user's attendee entry is found by token
  (`SHA1(UID + canonical address)`, the address canonicalised as Proton's web client guesses it,
  preferring a token already on the attendee) and its ID in the clear attendee list.
- Sends `PUT /calendar/v1/{id}/events/{event}/attendees/{attendee}` with `{Status, UpdateTime,
  Comment: null}`. When the answer moves between accepted or tentative and declined or not answered, it
  also re-sends the personal part (`PUT .../personal`) with the event's reminders and colour, as the web
  client does.
- A response to one occurrence of a recurring invitation is `unsupported`; `recurrence_id` must be null.
- The same answer as the current one returns `written` false and touches nothing. A failed personal-part
  update after the answer landed is the warning `personal_part_not_updated`, not a failure.
- `not_attendee` for the user's own event. The result adds `organiser_sees_answer`, true when the event
  is a Proton-to-Proton invitation, and `shown_by_provider`, the people who see the answer without any
  mail: `[{"name", "email", "is_self"}]`, that invitation's organiser, or empty.

### `send_itip`

The only operation that touches a mail endpoint. Args:

```json
{
  "calendar_id": "...",
  "method": "REQUEST",
  "ics": "...the itip.ics from a write result...",
  "recurrence_id": null,
  "recipients": [{"email": "bob@example.org", "name": "Bob Example"}],
  "subject": "Invitation: Design review @ Tue 1 Dec 14:00",
  "body": "Alice Example has invited you to Design review.",
  "response": null,
  "note": null
}
```

- `method` is `REQUEST` (invitation or update), `CANCEL` (cancellation, or removal of the recipients)
  or `REPLY` (the user's answer; `response` and optional `note`, sent to the organiser).
- `recurrence_id` narrows a `CANCEL` to one occurrence (`RECURRENCE-ID`, no `RRULE`).
- The attachment is `invite.ics`, `text/calendar; method=...`, with `X-PM-TOKEN` parameters,
  `X-NOJOIN-HOLD` and the organiser's private `COMMENT` removed. A `CANCEL` lists the recipients as
  attendees and carries `STATUS:CANCELLED`; a `REPLY` lists only the user with `PARTSTAT`, and
  `COMMENT` when `note` is given.
- Mail goes as Proton's clients send it: a draft encrypted to the sender's address key, the encrypted
  attachment, then send, with the recipients split into an internal package (Proton addresses, found
  by `GET /core/v4/keys`, encrypted to their keys) and a clear package for everyone else. `subject` and
  `body` are the caller's (at most 255 and 10000 characters, no line break in the subject).
- Result: `{"message_id": "...", "sent": [{"email": "...", "scheme": "internal"}], "failed":
  [{"email": "...", "error": "provider_rejected"}], "skipped": [{"email": "...", "reason": "is_you"}]}`.
  A recipient whose keys cannot be looked up is listed under `failed` and not sent in the clear. When
  the draft or the send itself fails, the whole operation fails.

### `validate`

No session, no network. Args: `{"event": {...event fields...}}` for a create, or
`{"patch": {...}, "all_day": false}` for an update. Result: `{"problems": [{"field": "event.description",
"code": "invalid_argument", "message": "..."}]}`, empty when the event fits Proton's limits. The write
operations run the same checks and fail with the first problem.
