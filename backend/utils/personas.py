"""Holiday personas: a light tone for Chat, Meeting Edge and, when asked for, notes.

Each persona is one file, ``backend/prompts/personas/<slug>.toml``, read with
tomllib:

- ``name``: the day, as shown in a tooltip ("Talk Like a Pirate Day");
- ``slug``: the file's own name, and what ``/persona <name>`` takes;
- ``order``: the persona's place in the list; on a day two personas share, the
  earlier one wins;
- ``date`` ("MM-DD") or ``rule`` ("first Saturday of February", "Saturday
  nearest 06-17"), exactly one of them;
- ``tooltip``: what the egg icon says;
- ``chat``, ``edge``, ``notes``: the tone line for each surface.

The files are read once. A file that cannot be read or breaks these rules is
skipped with a warning; the others still load. The tone sent to a model always
ends with ``PERSONA_SAFETY``, so no file can leave it out.
"""

from __future__ import annotations

import logging
import re
import tomllib
from dataclasses import dataclass
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, Mapping, Optional

from backend.utils.prompt_blocks import PromptBlock

logger = logging.getLogger(__name__)

PERSONAS_DIR = Path(__file__).resolve().parent.parent / "prompts" / "personas"

Surface = Literal["chat", "edge", "notes"]
SURFACES: tuple[Surface, ...] = ("chat", "edge", "notes")
Reason = Literal["dated", "command"]

TONE_HEADING = "# Tone for Today"
PERSONA_SAFETY = (
    "The persona affects tone only and must never change facts, numbers, names "
    "or quotes from the meeting."
)
NOTES_GUARD = (
    "Keep it to the prose: every heading, section, list and table stays exactly "
    "as the notes format requires, and decisions and action items are stated "
    "plainly."
)

_WEEKDAYS = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)
_MONTHS = (
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
)
_MONTH_DAY = re.compile(r"(\d{2})-(\d{2})")
_FIRST_WEEKDAY_OF = re.compile(r"first ([a-z]+) of ([a-z]+)")
_WEEKDAY_NEAREST = re.compile(r"([a-z]+) nearest (\d{2}-\d{2})")
_SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_TEXT_FIELDS = ("name", "tooltip", "chat", "edge", "notes")


@dataclass(frozen=True)
class DayRule:
    """When a persona's day falls.

    A fixed date has no ``weekday``. "First <weekday> of <month>" has
    ``weekday`` and no ``day``. "<Weekday> nearest MM-DD" has both: the
    weekday within three days of that date, in the same year.
    """

    month: int
    day: Optional[int] = None
    weekday: Optional[int] = None

    def matches(self, today: date) -> bool:
        if self.weekday is None:
            return (today.month, today.day) == (self.month, self.day)
        if today.weekday() != self.weekday:
            return False
        if self.day is None:
            return today.month == self.month and today.day <= 7
        anchor = date(today.year, self.month, self.day)
        return abs(today - anchor) <= timedelta(days=3)


def _month_day(value: str) -> tuple[int, int]:
    match = _MONTH_DAY.fullmatch(value)
    if match is None:
        raise ValueError(f"date {value!r} is not MM-DD")
    month, day = int(match.group(1)), int(match.group(2))
    # A leap year, so 02-29 is accepted as a date.
    date(2000, month, day)
    return month, day


def _weekday(name: str) -> int:
    if name not in _WEEKDAYS:
        raise ValueError(f"{name!r} is not a weekday")
    return _WEEKDAYS.index(name)


def parse_rule(rule: str) -> DayRule:
    """A ``rule`` string as a DayRule; ValueError when it is not one we know."""
    text = " ".join(rule.lower().split())
    first = _FIRST_WEEKDAY_OF.fullmatch(text)
    if first is not None:
        month_name = first.group(2)
        if month_name not in _MONTHS:
            raise ValueError(f"{month_name!r} is not a month")
        return DayRule(
            month=_MONTHS.index(month_name) + 1, weekday=_weekday(first.group(1))
        )
    nearest = _WEEKDAY_NEAREST.fullmatch(text)
    if nearest is not None:
        month, day = _month_day(nearest.group(2))
        if (month, day) == (2, 29):
            raise ValueError("a nearest rule cannot use 02-29")
        return DayRule(month=month, day=day, weekday=_weekday(nearest.group(1)))
    raise ValueError(f"rule {rule!r} is not understood")


@dataclass(frozen=True)
class Persona:
    slug: str
    name: str
    order: int
    when: DayRule
    tooltip: str
    chat: str
    edge: str
    notes: str

    def line(self, surface: Surface) -> str:
        return {"chat": self.chat, "edge": self.edge, "notes": self.notes}[surface]


@dataclass(frozen=True)
class ActivePersona:
    """A persona that applies, and why: today's date, or ``/persona``."""

    persona: Persona
    reason: Reason

    def tone(self, surface: Surface) -> str:
        """The ``# Tone for Today`` text for ``surface``, safety line included."""
        parts = [self.persona.line(surface), PERSONA_SAFETY]
        if surface == "notes":
            parts.append(NOTES_GUARD)
        return " ".join(parts)

    def reason_text(self, surface: Surface) -> str:
        if surface == "notes":
            if self.reason == "dated":
                return f"Written on {self.persona.name}."
            return "Written with /persona."
        if self.reason == "dated":
            return f"Today is {self.persona.name}."
        return "Set with /persona."


def tone_block(tone: Optional[str]) -> PromptBlock:
    """The one prompt block every surface uses; empty, and so dropped, without a tone."""
    return (TONE_HEADING, tone)


def _persona_from_data(data: Mapping[str, Any], stem: str) -> Persona:
    for key in ("slug", *_TEXT_FIELDS):
        value = data.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{key} must be a non-empty string")
    slug = data["slug"]
    if slug != stem or _SLUG.fullmatch(slug) is None:
        raise ValueError(f"slug {slug!r} must be lowercase and match the file name")
    order = data.get("order")
    if not isinstance(order, int) or isinstance(order, bool):
        raise ValueError("order must be an integer")
    fixed, rule = data.get("date"), data.get("rule")
    if (fixed is None) == (rule is None):
        raise ValueError("exactly one of date and rule is required")
    if fixed is not None:
        if not isinstance(fixed, str):
            raise ValueError("date must be a string")
        month, day = _month_day(fixed)
        when = DayRule(month=month, day=day)
    else:
        if not isinstance(rule, str):
            raise ValueError("rule must be a string")
        when = parse_rule(rule)
    return Persona(
        slug=slug,
        order=order,
        when=when,
        **{key: data[key].strip() for key in _TEXT_FIELDS},
    )


def load_personas(directory: Path) -> tuple[Persona, ...]:
    """Every valid persona file in ``directory``, in list order."""
    loaded: dict[str, Persona] = {}
    for path in sorted(directory.glob("*.toml")):
        try:
            with path.open("rb") as handle:
                persona = _persona_from_data(tomllib.load(handle), path.stem)
        except (OSError, tomllib.TOMLDecodeError, ValueError) as exc:
            logger.warning("Skipping persona file %s: %s", path.name, exc)
            continue
        loaded[persona.slug] = persona
    return tuple(sorted(loaded.values(), key=lambda persona: persona.order))


@lru_cache(maxsize=1)
def personas() -> tuple[Persona, ...]:
    """The shipped personas, read once per process."""
    return load_personas(PERSONAS_DIR)


def persona_by_slug(slug: str) -> Optional[Persona]:
    wanted = slug.lower()
    return next((persona for persona in personas() if persona.slug == wanted), None)


def persona_for_day(today: date) -> Optional[Persona]:
    """Today's persona; when two share the day, the earlier in the list."""
    return next(
        (persona for persona in personas() if persona.when.matches(today)), None
    )
