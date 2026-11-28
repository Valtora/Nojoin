"""A stand-in for the nojoin-proton binary, for tests of the Python side.

``install_fake_helper`` writes a small Python script that answers each
operation from a queue of canned responses and logs every request it gets,
argv and environment included, so tests can check what crossed the process
boundary. Point ``NOJOIN_PROTON_HELPER`` at it. Every account in it is made up.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

_SCRIPT = """#!{python}
import json, os, sys
state_path = {state!r}
log_path = {log!r}
request = json.loads(sys.stdin.read())
with open(state_path) as handle:
    state = json.load(handle)
queue = state.get(request["op"]) or [{{"ok": False, "error": {{"code": "invalid_request", "message": "no answer queued"}}}}]
if len(queue) > 1:
    answer = queue.pop(0)
    state[request["op"]] = queue
    with open(state_path, "w") as handle:
        json.dump(state, handle)
else:
    answer = queue[0]
with open(log_path, "a") as handle:
    handle.write(json.dumps({{"request": request, "argv": sys.argv, "env": dict(os.environ)}}) + "\\n")
if answer.get("sleep"):
    import time
    time.sleep(answer["sleep"])
if answer.get("exit"):
    sys.exit(answer["exit"])
if "raw" in answer:
    sys.stdout.write(answer["raw"])
else:
    sys.stdout.write(json.dumps({{"v": 1, **answer}}))
"""


class FakeHelper:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.state = directory / "state.json"
        self.log = directory / "requests.jsonl"
        self.path = directory / "nojoin-proton"
        self.state.write_text("{}")
        self.path.write_text(
            _SCRIPT.format(
                python=sys.executable, state=str(self.state), log=str(self.log)
            )
        )
        self.path.chmod(0o700)

    def answer(self, op: str, *answers: dict[str, Any]) -> None:
        """Queue answers for an operation; the last one repeats."""
        state = json.loads(self.state.read_text())
        state[op] = [dict(answer) for answer in answers]
        self.state.write_text(json.dumps(state))

    def ok(self, op: str, *results: dict[str, Any]) -> None:
        self.answer(op, *({"ok": True, "result": result} for result in results))

    def fail(self, op: str, code: str, **error: Any) -> None:
        self.answer(
            op, {"ok": False, "error": {"code": code, "message": code, **error}}
        )

    def calls(self) -> list[dict[str, Any]]:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def ops(self) -> list[str]:
        return [call["request"]["op"] for call in self.calls()]


def install_fake_helper(tmp_path: Path, monkeypatch: Any) -> FakeHelper:
    helper = FakeHelper(tmp_path)
    monkeypatch.setenv("NOJOIN_PROTON_HELPER", str(helper.path))
    return helper


def helper_event(uid: str, **fields: Any) -> dict[str, Any]:
    """One event record as the helper returns it (PROTOCOL.md)."""
    recurrence_id = fields.pop("recurrence_id", None)
    record = {
        "provider_event_id": f"{uid}#{recurrence_id}" if recurrence_id else uid,
        "uid": uid,
        "title": "Design review",
        "description": None,
        "location": None,
        "meeting_url": None,
        "url": None,
        "is_all_day": False,
        "starts_at": "2026-12-01T14:00:00Z",
        "ends_at": "2026-12-01T15:00:00Z",
        "start_date": None,
        "end_date": None,
        "start_time_zone": "Europe/London",
        "end_time_zone": "Europe/London",
        "status": "confirmed",
        "is_hold": False,
        "show_as": "busy",
        "organiser": {
            "name": "Alice Example",
            "email": "alice@example.com",
            "is_self": True,
        },
        "is_organiser": True,
        "my_response": None,
        "attendees": [],
        "attendee_count": 0,
        "series_key": uid if recurrence_id else None,
        "recurrence_rule": None,
        "occurrence_type": "occurrence" if recurrence_id else "single",
        "original_start": None,
        "original_start_date": None,
        "sequence": 0,
        "revision": "p1:1:0",
        "created_at": "2026-11-28T10:00:00Z",
        "updated_at": "2026-11-28T10:00:00Z",
        "decrypt_warnings": [],
        "provider_data": {
            "uid": uid,
            "calendar_id": "cal-proton",
            "event_id": f"row-{uid}",
            "master_event_id": f"row-{uid}",
            "exception_event_id": None,
        },
    }
    record.update(fields)
    return record


def calendars_result() -> dict[str, Any]:
    return {
        "settings": {
            "default_calendar_id": "cal-proton",
            "primary_time_zone": "Europe/London",
        },
        "calendars": [
            {
                "id": "cal-proton",
                "name": "Personal",
                "description": "",
                "colour": "#5252cc",
                "type": "personal",
                "writable": True,
                "read_only_reason": None,
                "is_default": True,
                "time_zone": "Europe/London",
            },
            {
                "id": "cal-holidays",
                "name": "Holidays",
                "type": "holidays",
                "writable": False,
                "read_only_reason": "calendar_read_only",
                "is_default": False,
                "time_zone": "Europe/London",
            },
        ],
    }
