"""The two Meeting Chat commands that steer holiday personas.

``/persona`` picks this meeting's persona; ``/eastereggs`` turns dated
personas on or off and chooses the surfaces. A message whose first word is
either command (any case) never goes to the model: one of the forms in
``USAGE`` is carried out, and anything else is answered with the usage line and
the current state. Other text starting with "/" goes to the model.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Mapping, Optional

from backend.utils.persona_settings import (
    PersonaPreferences,
    read_preferences,
    with_preferences,
)
from backend.utils.personas import SURFACES, persona_by_slug, personas

TRUTHY = frozenset({"on", "true", "enabled", "yes"})
FALSY = frozenset({"off", "false", "disabled", "no"})
PERSONA_OFF = frozenset({"off", "false", "disabled"})
SURFACE_LABELS: Mapping[str, str] = {
    "chat": "Chat",
    "edge": "Meeting Edge",
    "notes": "notes",
}
USAGE = (
    "Usage: /persona <name>, /persona off, /eastereggs on|off, "
    "/eastereggs chat|edge|notes on|off."
)


@dataclass(frozen=True)
class CommandResult:
    """The reply to post, and the settings to store when the command changed them."""

    reply: str
    settings: Optional[dict[str, Any]] = None


def _switch(word: str) -> Optional[bool]:
    if word in TRUTHY:
        return True
    if word in FALSY:
        return False
    return None


def _status(preferences: PersonaPreferences, recording_key: str) -> str:
    if preferences.enabled is False:
        dated = "Easter eggs: off, so no persona applies."
    else:
        dated = f"Dated personas: {'on' if preferences.enabled else 'off'}."
    surfaces = ", ".join(
        f"{SURFACE_LABELS[surface]} {'on' if preferences.surface_on(surface) else 'off'}"
        for surface in SURFACES
    )
    meeting = preferences.meetings.get(recording_key) or "none"
    return f"{dated} Surfaces: {surfaces}. This meeting: {meeting}. {USAGE}"


def _set_persona(
    settings: Mapping[str, Any],
    preferences: PersonaPreferences,
    recording_key: str,
    name: str,
    status: str,
) -> CommandResult:
    meetings = {k: v for k, v in preferences.meetings.items() if k != recording_key}
    if name in PERSONA_OFF:
        updated = replace(preferences, meetings=meetings)
        return CommandResult(
            "Persona for this meeting: off.", with_preferences(settings, updated)
        )
    persona = persona_by_slug(name)
    if persona is None:
        names = ", ".join(persona.slug for persona in personas())
        return CommandResult(
            f'Unknown persona "{name}". Valid names: {names}. {status}'
        )
    meetings[recording_key] = persona.slug
    reply = f"Persona for this meeting: {persona.slug}."
    if preferences.enabled is False:
        reply += " Easter eggs are off, so it applies once you send /eastereggs on."
    return CommandResult(
        reply, with_preferences(settings, replace(preferences, meetings=meetings))
    )


def _set_eastereggs(
    settings: Mapping[str, Any], preferences: PersonaPreferences, words: list[str]
) -> Optional[CommandResult]:
    if len(words) == 1:
        enabled = _switch(words[0])
        if enabled is None:
            return None
        updated = replace(preferences, enabled=enabled)
        return CommandResult(
            f"Holiday personas {'on' if enabled else 'off'}.",
            with_preferences(settings, updated),
        )
    surface, on = words[0], _switch(words[1])
    if surface not in SURFACES or on is None:
        return None
    surfaces = {**preferences.surfaces, surface: on}
    updated = replace(preferences, surfaces=surfaces)
    label = SURFACE_LABELS[surface]
    return CommandResult(
        f"Holiday personas in {label}: {'on' if on else 'off'}.",
        with_preferences(settings, updated),
    )


def run_persona_command(
    message: str, settings: Optional[Mapping[str, Any]], recording_id: int
) -> Optional[CommandResult]:
    """The command's result, or None when ``message`` is not a command."""
    words = message.strip().lower().split()
    if not words or words[0] not in ("/persona", "/eastereggs"):
        return None
    command, arguments = words[0], words[1:]
    current = dict(settings or {})
    preferences = read_preferences(current)
    recording_key = str(recording_id)
    status = _status(preferences, recording_key)
    if not arguments:
        return CommandResult(status)
    result = None
    if command == "/persona" and len(arguments) == 1:
        result = _set_persona(current, preferences, recording_key, arguments[0], status)
    elif command == "/eastereggs" and len(arguments) <= 2:
        result = _set_eastereggs(current, preferences, arguments)
    return result or CommandResult(f"That command was not understood. {status}")
