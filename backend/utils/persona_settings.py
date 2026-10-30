"""Per-user holiday persona state, kept in the user's settings, and which persona applies.

Stored under ``SETTINGS_KEY`` in ``User.settings``, so it needs no table of its
own. The Settings page never sees it (``GET /settings`` drops the key) and only
the ``/persona`` and ``/eastereggs`` chat commands change it::

    {
        "enabled": true,                      # absent until /eastereggs on|off
        "surfaces": {"chat": true, "edge": true, "notes": false},
        "meetings": {"<recording id>": "pirate"},
        "notes": {"<recording id>": {"slug": "pirate", "reason": "dated"}},
    }

``enabled`` has three states. Absent: dated personas are off, and a meeting's
``/persona`` still applies. True: dated personas are on too. False: no persona
applies anywhere, though the meetings' choices are kept. ``notes`` records the
persona the meeting's current notes were written with, for the Notes tab.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, date
from typing import Any, Mapping, Optional

from sqlmodel import Session

from backend.models.user import User
from backend.utils.personas import (
    SURFACES,
    ActivePersona,
    Surface,
    persona_by_slug,
    persona_for_day,
)
from backend.utils.time import utc_now
from backend.utils.timezones import get_timezone, get_user_timezone_name

SETTINGS_KEY = "holiday_personas"
DEFAULT_SURFACES: Mapping[str, bool] = {"chat": True, "edge": True, "notes": False}
_REASONS = ("dated", "command")


@dataclass(frozen=True)
class PersonaPreferences:
    enabled: Optional[bool] = None
    surfaces: Mapping[str, bool] = field(default_factory=lambda: dict(DEFAULT_SURFACES))
    meetings: Mapping[str, str] = field(default_factory=dict)
    notes: Mapping[str, Mapping[str, str]] = field(default_factory=dict)

    def surface_on(self, surface: Surface) -> bool:
        return bool(self.surfaces.get(surface, DEFAULT_SURFACES[surface]))


def read_preferences(settings: Optional[Mapping[str, Any]]) -> PersonaPreferences:
    """The stored state, with anything malformed read as its default."""
    raw = (settings or {}).get(SETTINGS_KEY)
    if not isinstance(raw, Mapping):
        return PersonaPreferences()
    enabled = raw.get("enabled")
    raw_surfaces = raw.get("surfaces")
    surfaces = dict(DEFAULT_SURFACES)
    if isinstance(raw_surfaces, Mapping):
        surfaces.update(
            {
                name: value
                for name, value in raw_surfaces.items()
                if name in SURFACES and isinstance(value, bool)
            }
        )
    raw_meetings = raw.get("meetings")
    meetings = (
        {
            key: value
            for key, value in raw_meetings.items()
            if isinstance(key, str) and isinstance(value, str)
        }
        if isinstance(raw_meetings, Mapping)
        else {}
    )
    raw_notes = raw.get("notes")
    notes = (
        {
            key: {"slug": entry["slug"], "reason": entry["reason"]}
            for key, entry in raw_notes.items()
            if isinstance(entry, Mapping)
            and isinstance(entry.get("slug"), str)
            and entry.get("reason") in _REASONS
        }
        if isinstance(raw_notes, Mapping)
        else {}
    )
    return PersonaPreferences(
        enabled=enabled if isinstance(enabled, bool) else None,
        surfaces=surfaces,
        meetings=meetings,
        notes=notes,
    )


def with_preferences(
    settings: Optional[Mapping[str, Any]], preferences: PersonaPreferences
) -> dict[str, Any]:
    """A copy of ``settings`` holding ``preferences``; ``settings`` is untouched."""
    stored: dict[str, Any] = {
        "surfaces": dict(preferences.surfaces),
        "meetings": dict(preferences.meetings),
        "notes": {key: dict(entry) for key, entry in preferences.notes.items()},
    }
    if preferences.enabled is not None:
        stored["enabled"] = preferences.enabled
    return {**(settings or {}), SETTINGS_KEY: stored}


def user_today(settings: Optional[Mapping[str, Any]]) -> date:
    """Today in the user's time zone (the install's default zone when unset)."""
    zone = get_timezone(get_user_timezone_name(settings))
    return utc_now().replace(tzinfo=UTC).astimezone(zone).date()


def active_persona(
    settings: Optional[Mapping[str, Any]],
    recording_id: int,
    surface: Surface,
    *,
    today: Optional[date] = None,
) -> Optional[ActivePersona]:
    """The persona that applies to ``surface`` of this recording now, if any.

    The meeting's own ``/persona`` first, then today's dated persona when dated
    personas are on. Nothing while easter eggs are off or the surface is.
    """
    preferences = read_preferences(settings)
    if preferences.enabled is False or not preferences.surface_on(surface):
        return None
    chosen = preferences.meetings.get(str(recording_id))
    persona = persona_by_slug(chosen) if chosen else None
    if persona is not None:
        return ActivePersona(persona, "command")
    if preferences.enabled:
        dated = persona_for_day(today or user_today(settings))
        if dated is not None:
            return ActivePersona(dated, "dated")
    return None


def surface_tone(
    settings: Optional[Mapping[str, Any]], recording_id: int, surface: Surface
) -> Optional[str]:
    """The tone text for ``surface`` of this recording, or None."""
    active = active_persona(settings, recording_id, surface)
    return active.tone(surface) if active else None


def notes_persona(
    settings: Optional[Mapping[str, Any]], recording_id: int
) -> Optional[ActivePersona]:
    """The persona this recording's current notes were written with."""
    entry = read_preferences(settings).notes.get(str(recording_id))
    persona = persona_by_slug(entry["slug"]) if entry else None
    if entry is None or persona is None:
        return None
    return ActivePersona(
        persona, "command" if entry["reason"] == "command" else "dated"
    )


def recording_personas(
    settings: Optional[Mapping[str, Any]], recording_id: int
) -> dict[Surface, Optional[ActivePersona]]:
    """What each surface of this recording shows an egg for."""
    today = user_today(settings)
    return {
        "chat": active_persona(settings, recording_id, "chat", today=today),
        "edge": active_persona(settings, recording_id, "edge", today=today),
        "notes": notes_persona(settings, recording_id),
    }


def personas_signature(personas: Mapping[str, Optional[ActivePersona]]) -> str:
    """A short stamp of ``recording_personas``; empty when no surface has one."""
    if not any(personas.values()):
        return ""
    return ",".join(
        f"{active.persona.slug}.{active.reason}" if active else ""
        for active in personas.values()
    )


def record_notes_persona(
    session: Session,
    user_id: Optional[int],
    recording_id: int,
    active: Optional[ActivePersona],
) -> None:
    """Note which persona (or none) the notes just generated were written with.

    The user row is read again first: notes take minutes, and the settings the
    task read when it started may have changed since. Nothing is written for a
    user whose record would not change.
    """
    user = session.get(User, user_id) if user_id is not None else None
    if user is None:
        return
    session.refresh(user)
    preferences = read_preferences(user.settings)
    key = str(recording_id)
    entry = {"slug": active.persona.slug, "reason": active.reason} if active else None
    if preferences.notes.get(key) == entry:
        return
    notes = {k: v for k, v in preferences.notes.items() if k != key}
    if entry is not None:
        notes[key] = entry
    user.settings = with_preferences(user.settings, replace(preferences, notes=notes))
    session.add(user)
